"""Scoped secret resolver seam for the MVP runtime.

The router sends only an opaque reference and exact version. In Compose the
reference resolves from a read-only Docker secret file. The implementation can
later be replaced by an OpenBao resolver without changing tool signatures.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from .errors import ConnectorError, ErrorCode

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,510}$")
_MAX_FILE_BYTES = 1024 * 1024


class SecretResolver(Protocol):
    async def resolve(self, reference: str, version: int) -> Mapping[str, str]: ...


class JsonFileSecretResolver:
    """Resolve exact versions from a root-owned/read-only JSON secret mount.

    Expected shape::

        {"secrets": {"dotykacka/workspace/installation": {
          "versions": {"1": {"refresh_token": "...", "cloud_id": "...",
                              "pii_key": "..."}}}}}

    Secret values are never included in exceptions or logs.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        configured = path or os.environ.get("OPENMCP_SECRET_STORE_FILE")
        if not configured:
            raise RuntimeError("OPENMCP_SECRET_STORE_FILE is required for this connector")
        self._path = Path(configured)

    async def resolve(self, reference: str, version: int) -> Mapping[str, str]:
        if not _REF_RE.fullmatch(reference) or ".." in reference.split("/"):
            raise ConnectorError(ErrorCode.FORBIDDEN, "Neplatný odkaz na credentials.")
        try:
            if self._path.stat().st_size > _MAX_FILE_BYTES:
                raise ConnectorError(ErrorCode.INTERNAL, "Úložiště credentials je příliš velké.")
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except ConnectorError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ConnectorError(
                ErrorCode.INTERNAL, "Credentials úložiště není dostupné."
            ) from exc
        try:
            values = raw["secrets"][reference]["versions"][str(version)]
        except (KeyError, TypeError) as exc:
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID,
                "Publikovaná verze credentials není dostupná.",
            ) from exc
        if not isinstance(values, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in values.items()
        ):
            raise ConnectorError(ErrorCode.INTERNAL, "Credentials mají neplatný formát.")
        return values


@dataclass
class _BaoToken:
    value: str
    expires_at: float
    renewable: bool


class OpenBaoSecretResolver:
    """OpenBao KV v2 exact-version resolver authenticated with AppRole.

    Public references use ``dotykacka/{workspace}/{installation}`` and are
    mapped to ``secret/data/workspaces/{workspace}/installations/{installation}/provider``.
    Neither AppRole material, client tokens nor KV values are logged or placed
    in exception messages.
    """

    _SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    _MAX_ID_FILE_BYTES = 4096
    _MAX_TOKEN_TTL = 1800
    _RENEW_WINDOW = 60

    def __init__(
        self,
        address: str,
        role_id_file: str | os.PathLike[str],
        secret_id_file: str | os.PathLike[str],
        *,
        mount: str = "secret",
        timeout: float = 5.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(address)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "openbao",
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            (parsed.scheme != "https" and not local_http)
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("OPENBAO_ADDR must be HTTPS (Compose may use http://openbao)")
        if not self._SEGMENT_RE.fullmatch(mount):
            raise RuntimeError("OPENBAO_KV_MOUNT is invalid")
        self._role_id_file = Path(role_id_file)
        self._secret_id_file = Path(secret_id_file)
        self._mount = mount
        self._client = httpx.AsyncClient(
            base_url=address.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 3.0)),
            follow_redirects=False,
            verify=verify,
            transport=transport,
        )
        self._token: _BaoToken | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> OpenBaoSecretResolver:
        address = os.environ.get("OPENBAO_ADDR")
        role_file = os.environ.get("OPENBAO_ROLE_ID_FILE")
        secret_file = os.environ.get("OPENBAO_SECRET_ID_FILE")
        if not address or not role_file or not secret_file:
            raise RuntimeError(
                "OPENBAO_ADDR, OPENBAO_ROLE_ID_FILE and OPENBAO_SECRET_ID_FILE are required"
            )
        ca_file = os.environ.get("OPENBAO_CACERT")
        return cls(
            address,
            role_file,
            secret_file,
            mount=os.environ.get("OPENBAO_KV_MOUNT", "secret"),
            verify=ca_file or True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _read_id(self, path: Path) -> str:
        try:
            if path.stat().st_size > self._MAX_ID_FILE_BYTES:
                raise ConnectorError(ErrorCode.INTERNAL, "OpenBao AppRole soubor je neplatný.")
            value = path.read_text(encoding="utf-8").strip()
        except ConnectorError:
            raise
        except OSError as exc:
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao AppRole není dostupná.") from exc
        if not value or len(value) > self._MAX_ID_FILE_BYTES:
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao AppRole soubor je neplatný.")
        return value

    @classmethod
    def _reference_path(cls, reference: str) -> str:
        parts = reference.split("/")
        if (
            len(parts) != 3
            or parts[0] != "dotykacka"
            or not cls._SEGMENT_RE.fullmatch(parts[1])
            or not cls._SEGMENT_RE.fullmatch(parts[2])
        ):
            raise ConnectorError(ErrorCode.FORBIDDEN, "Neplatný odkaz na credentials.")
        workspace = quote(parts[1], safe="")
        installation = quote(parts[2], safe="")
        return f"workspaces/{workspace}/installations/{installation}/provider"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: dict[str, object] | None = None,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> dict[str, object]:
        headers = {"X-Vault-Token": token} if token else None
        try:
            response = await self._client.request(
                method, path, headers=headers, json=json_body, params=params
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ConnectorError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "Úložiště credentials je dočasně nedostupné.",
                retryable=True,
            ) from exc
        if response.status_code in (401, 403):
            raise ConnectorError(ErrorCode.FORBIDDEN, "OpenBao autorizace byla odmítnuta.")
        if response.status_code == 404:
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID, "Publikovaná verze credentials není dostupná."
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ConnectorError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "Úložiště credentials je dočasně nedostupné.",
                retryable=True,
            )
        if response.status_code >= 400:
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao požadavek byl odmítnut.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao vrátil neplatnou odpověď.") from exc
        if not isinstance(payload, dict):
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao vrátil neplatnou odpověď.")
        return payload

    @classmethod
    def _parse_token(cls, payload: Mapping[str, object]) -> _BaoToken:
        auth = payload.get("auth")
        if not isinstance(auth, dict):
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao nevydal klientský token.")
        token = auth.get("client_token")
        lease = auth.get("lease_duration")
        renewable = auth.get("renewable", False)
        if not isinstance(token, str) or not token:
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao nevydal klientský token.")
        ttl = lease if isinstance(lease, int) and not isinstance(lease, bool) else 300
        ttl = max(30, min(ttl, cls._MAX_TOKEN_TTL))
        return _BaoToken(token, time.monotonic() + ttl, renewable is True)

    async def _login(self) -> _BaoToken:
        payload = await self._request(
            "POST",
            "/v1/auth/approle/login",
            json_body={
                "role_id": self._read_id(self._role_id_file),
                "secret_id": self._read_id(self._secret_id_file),
            },
        )
        return self._parse_token(payload)

    async def _client_token(self) -> str:
        now = time.monotonic()
        cached = self._token
        if cached is not None and now < cached.expires_at - self._RENEW_WINDOW:
            return cached.value
        async with self._lock:
            now = time.monotonic()
            cached = self._token
            if cached is not None and now < cached.expires_at - self._RENEW_WINDOW:
                return cached.value
            if cached is not None and cached.renewable and now < cached.expires_at:
                try:
                    payload = await self._request(
                        "POST",
                        "/v1/auth/token/renew-self",
                        token=cached.value,
                        json_body={"increment": 300},
                    )
                    self._token = self._parse_token(payload)
                    return self._token.value
                except ConnectorError as exc:
                    if exc.code not in (ErrorCode.FORBIDDEN, ErrorCode.CREDENTIAL_INVALID):
                        raise
            self._token = await self._login()
            return self._token.value

    async def resolve(self, reference: str, version: int) -> Mapping[str, str]:
        if version < 1:
            raise ConnectorError(ErrorCode.FORBIDDEN, "Neplatná verze credentials.")
        path = self._reference_path(reference)
        token = await self._client_token()
        try:
            payload = await self._request(
                "GET",
                f"/v1/{self._mount}/data/{path}",
                token=token,
                params={"version": version},
            )
        except ConnectorError as exc:
            if exc.code != ErrorCode.FORBIDDEN:
                raise
            # The cached token may have been revoked. Re-login once; never retry KV errors.
            async with self._lock:
                self._token = None
            token = await self._client_token()
            payload = await self._request(
                "GET",
                f"/v1/{self._mount}/data/{path}",
                token=token,
                params={"version": version},
            )
        outer = payload.get("data")
        if not isinstance(outer, dict):
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao KV odpověď je neplatná.")
        metadata = outer.get("metadata")
        values = outer.get("data")
        if (
            not isinstance(metadata, dict)
            or metadata.get("version") != version
            or not isinstance(values, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in values.items()
            )
        ):
            raise ConnectorError(ErrorCode.INTERNAL, "OpenBao KV odpověď je neplatná.")
        return values
