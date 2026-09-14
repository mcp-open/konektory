"""Signed authorization for the core-to-connector invocation boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .replay import InMemoryReplayStore, ReplayStore

INVOCATION_TOKEN_ISSUER = "openmcp-core"
INVOCATION_TOKEN_TYPE = "OMCP-INV"
INVOCATION_TOKEN_VERSION = 1
MAX_INVOCATION_TOKEN_TTL_SECONDS = 60
_MAX_TOKEN_BYTES = 8 * 1024
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_CLAIM_KEYS = {
    "iss",
    "aud",
    "iat",
    "exp",
    "jti",
    "request_id",
    "subject",
    "workspace_id",
    "installation_id",
    "connector",
    "manifest_version",
    "tool",
    "body_sha256",
}


class TokenValidationError(ValueError):
    """A deliberately detail-free invocation authorization failure."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TokenValidationError("duplicate JSON member")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def loads_json_no_duplicates(raw: bytes) -> Any:
    """Decode UTF-8 JSON while rejecting ambiguous duplicate object members."""

    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, TokenValidationError) as exc:
        raise TokenValidationError("invalid JSON") from exc


def _decode_segment(segment: str, *, max_decoded_bytes: int) -> bytes:
    if not segment or not _BASE64URL.fullmatch(segment) or "=" in segment:
        raise TokenValidationError("invalid token encoding")
    padding = "=" * (-len(segment) % 4)
    try:
        decoded = base64.b64decode(
            (segment + padding).encode("ascii"), altchars=b"-_", validate=True
        )
    except (UnicodeEncodeError, ValueError) as exc:
        raise TokenValidationError("invalid token encoding") from exc
    if len(decoded) > max_decoded_bytes or _encode_segment(decoded) != segment:
        raise TokenValidationError("invalid token encoding")
    return decoded


def _encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def extract_bearer(raw_headers: Iterable[tuple[bytes, bytes]]) -> str:
    """Return exactly one strict Bearer credential from ASGI raw headers."""

    values = [value for name, value in raw_headers if name.lower() == b"authorization"]
    if len(values) != 1:
        raise TokenValidationError("invalid authorization")
    try:
        supplied = values[0].decode("ascii")
    except UnicodeDecodeError as exc:
        raise TokenValidationError("invalid authorization") from exc
    parts = supplied.split(" ")
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not parts[1]
        or len(parts[1].encode("ascii")) > _MAX_TOKEN_BYTES
    ):
        raise TokenValidationError("invalid authorization")
    return parts[1]


@dataclass(frozen=True)
class InvocationBinding:
    request_id: str
    subject: str
    workspace_id: str
    installation_id: str
    connector: str
    manifest_version: str
    tool: str


@dataclass(frozen=True)
class InvocationClaims:
    issuer: str
    audience: str
    issued_at: int
    expires_at: int
    jti: str
    request_id: str
    subject: str
    workspace_id: str
    installation_id: str
    connector: str
    manifest_version: str
    tool: str
    body_sha256: str


# Compatibility name for direct SDK users. Creating the runtime no longer
# selects it implicitly; production configuration must choose a shared store.
ReplayCache = InMemoryReplayStore


class InvocationTokenVerifier:
    """Verify signature, lifetime and exact request bindings, then consume JTI."""

    def __init__(
        self,
        signing_key: str | bytes,
        *,
        connector: str,
        manifest_version: str,
        replay_store: ReplayStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        key = signing_key.encode("utf-8") if isinstance(signing_key, str) else bytes(signing_key)
        if len(key) < 32:
            raise ValueError("invocation signing key must contain at least 32 bytes")
        if not connector or not manifest_version:
            raise ValueError("connector and manifest version are required")
        self._key = key
        self._connector = connector
        self._manifest_version = manifest_version
        self._replay_store = replay_store
        self._clock = clock

    def verify(self, token: str, binding: InvocationBinding, body: bytes) -> InvocationClaims:
        try:
            encoded = token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise TokenValidationError("invalid token") from exc
        if len(encoded) > _MAX_TOKEN_BYTES:
            raise TokenValidationError("invalid token")
        parts = token.split(".")
        if len(parts) != 3:
            raise TokenValidationError("invalid token")
        header_raw = _decode_segment(parts[0], max_decoded_bytes=512)
        payload_raw = _decode_segment(parts[1], max_decoded_bytes=4096)
        signature = _decode_segment(parts[2], max_decoded_bytes=32)
        if len(signature) != hashlib.sha256().digest_size:
            raise TokenValidationError("invalid signature")

        try:
            header = loads_json_no_duplicates(header_raw)
            payload = loads_json_no_duplicates(payload_raw)
        except TokenValidationError as exc:
            raise TokenValidationError("invalid token JSON") from exc
        if type(header) is not dict or set(header) != {"alg", "typ", "v"}:
            raise TokenValidationError("invalid token header")
        if (
            header["alg"] != "HS256"
            or header["typ"] != INVOCATION_TOKEN_TYPE
            or type(header["v"]) is not int
            or header["v"] != INVOCATION_TOKEN_VERSION
        ):
            raise TokenValidationError("invalid token header")
        if type(payload) is not dict or set(payload) != _CLAIM_KEYS:
            raise TokenValidationError("invalid token claims")

        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected_signature = hmac.new(self._key, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise TokenValidationError("invalid signature")

        claims = self._claims(payload)
        now = int(self._clock())
        if (
            claims.issued_at > now
            or claims.expires_at <= now
            or claims.expires_at <= claims.issued_at
            or claims.expires_at - claims.issued_at > MAX_INVOCATION_TOKEN_TTL_SECONDS
        ):
            raise TokenValidationError("invalid token lifetime")
        if claims.issuer != INVOCATION_TOKEN_ISSUER:
            raise TokenValidationError("invalid issuer")
        expected_audience = f"connector:{self._connector}"
        exact_bindings = (
            (claims.audience, expected_audience),
            (claims.request_id, binding.request_id),
            (claims.subject, binding.subject),
            (claims.workspace_id, binding.workspace_id),
            (claims.installation_id, binding.installation_id),
            (claims.connector, binding.connector),
            (claims.connector, self._connector),
            (claims.manifest_version, binding.manifest_version),
            (claims.manifest_version, self._manifest_version),
            (claims.tool, binding.tool),
        )
        if any(not hmac.compare_digest(actual, expected) for actual, expected in exact_bindings):
            raise TokenValidationError("invocation binding mismatch")
        expected_body_digest = _encode_segment(hashlib.sha256(body).digest())
        if not hmac.compare_digest(claims.body_sha256, expected_body_digest):
            raise TokenValidationError("invocation body digest mismatch")
        if not self._replay_store.consume(claims.jti, claims.expires_at, now):
            raise TokenValidationError("invocation token replayed or replay cache full")
        return claims

    @staticmethod
    def _claims(payload: dict[str, Any]) -> InvocationClaims:
        string_limits = {
            "iss": 64,
            "aud": 128,
            "jti": 128,
            "request_id": 128,
            "subject": 256,
            "workspace_id": 128,
            "installation_id": 128,
            "connector": 64,
            "manifest_version": 32,
            "tool": 128,
            "body_sha256": 64,
        }
        for name, maximum in string_limits.items():
            value = payload[name]
            if (
                type(value) is not str
                or not value
                or len(value) > maximum
                or any(character in value for character in "\x00\r\n")
            ):
                raise TokenValidationError("invalid token claim")
        if type(payload["iat"]) is not int or type(payload["exp"]) is not int:
            raise TokenValidationError("invalid token time claim")
        try:
            decoded_jti = _decode_segment(payload["jti"], max_decoded_bytes=64)
        except TokenValidationError as exc:
            raise TokenValidationError("invalid token ID") from exc
        if len(decoded_jti) < 16:
            raise TokenValidationError("invalid token ID")
        try:
            decoded_body_digest = _decode_segment(
                payload["body_sha256"], max_decoded_bytes=hashlib.sha256().digest_size
            )
        except TokenValidationError as exc:
            raise TokenValidationError("invalid body digest") from exc
        if len(decoded_body_digest) != hashlib.sha256().digest_size:
            raise TokenValidationError("invalid body digest")
        return InvocationClaims(
            issuer=payload["iss"],
            audience=payload["aud"],
            issued_at=payload["iat"],
            expires_at=payload["exp"],
            jti=payload["jti"],
            request_id=payload["request_id"],
            subject=payload["subject"],
            workspace_id=payload["workspace_id"],
            installation_id=payload["installation_id"],
            connector=payload["connector"],
            manifest_version=payload["manifest_version"],
            tool=payload["tool"],
            body_sha256=payload["body_sha256"],
        )
