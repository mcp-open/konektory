"""Bounded asynchronous upstream client with safe error mapping."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Iterable
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from .errors import ConnectorError, ErrorCode


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


class UpstreamClient:
    _MAX_JSON_DEPTH = 64
    _MAX_JSON_NODES = 100_000

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        connect_timeout: float = 3.0,
        max_attempts: int = 2,
        max_response_bytes: int = 4 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("upstream base_url must be a fixed HTTPS origin/path")
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max(1, min(max_attempts, 3))
        if not 1 <= max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1 byte and 16 MiB")
        self.max_response_bytes = max_response_bytes
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool = False,
    ) -> tuple[Any, str]:
        parsed_path = urlsplit(path)
        if (
            not path.startswith("/")
            or parsed_path.scheme
            or parsed_path.netloc
            or parsed_path.fragment
            or "\\" in path
            or ".." in unquote(parsed_path.path).split("/")
        ):
            raise ConnectorError(ErrorCode.INTERNAL, "Neplatná upstream cesta.")
        attempts = self.max_attempts if (method.upper() == "GET" or idempotent) else 1
        for attempt in range(attempts):
            response: httpx.Response | None = None
            try:
                request = self._client.build_request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                response = await self._client.send(request, stream=True)

                retryable_status = response.status_code == 429 or response.status_code >= 500
                if retryable_status and attempt + 1 < attempts:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue

                self._raise_for_status(response)
                raw = await self._read_bounded(response)
                try:
                    payload = json.loads(raw, parse_constant=_reject_json_constant)
                except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                    raise ConnectorError(
                        ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatnou odpověď."
                    ) from exc
                self._validate_json_complexity(payload)
                # Deliberately omit query parameters from provenance; they can contain user input.
                source_url = str(response.url.copy_with(query=None))
                return payload, source_url
            except httpx.DecodingError as exc:
                raise ConnectorError(
                    ErrorCode.UPSTREAM_ERROR,
                    "Poskytovatel vrátil neplatně zakódovanou odpověď.",
                ) from exc
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.05 * (attempt + 1))
                    continue
                raise ConnectorError(
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                    "Poskytovatel je dočasně nedostupný.",
                    retryable=True,
                ) from exc
            finally:
                if response is not None:
                    await response.aclose()

        raise AssertionError("upstream attempt loop returned no result")

    @classmethod
    def _validate_json_complexity(cls, payload: Any) -> None:
        """Reject provider-controlled structures unsafe for later validation/serialization."""
        stack: list[tuple[Any, int]] = [(payload, 1)]
        visited = 0
        while stack:
            value, depth = stack.pop()
            visited += 1
            if visited > cls._MAX_JSON_NODES or depth > cls._MAX_JSON_DEPTH:
                raise ConnectorError(
                    ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele je příliš složitá."
                )

            if isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ConnectorError(
                    ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele obsahuje neplatný text."
                )

            children: Iterable[Any]
            if isinstance(value, dict):
                children = value.values()
                child_count = len(value)
            elif isinstance(value, list):
                children = value
                child_count = len(value)
            else:
                continue

            # Bound the work queue before materializing references to every
            # child of a provider-controlled wide container.
            if visited + len(stack) + child_count > cls._MAX_JSON_NODES:
                raise ConnectorError(
                    ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele je příliš složitá."
                )
            child_depth = depth + 1
            stack.extend((child, child_depth) for child in children)

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        raw_length = response.headers.get("Content-Length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                declared_length = -1
            if declared_length > self.max_response_bytes:
                raise ConnectorError(
                    ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele je příliš velká."
                )

        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > self.max_response_bytes:
                raise ConnectorError(
                    ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele je příliš velká."
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After", "")
        try:
            seconds = float(raw)
            if not math.isfinite(seconds):
                raise ValueError("Retry-After is not finite")
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
                response_at = parsedate_to_datetime(response.headers["Date"])
                seconds = (retry_at - response_at).total_seconds()
            except (KeyError, TypeError, ValueError):
                seconds = 0.05 * (attempt + 1)
        return max(0.0, min(seconds, 0.5))

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 300:
            return
        if status == 404:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Záznam nebyl nalezen.", provider_status=404)
        if status in (401, 403):
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID,
                "Poskytovatel odmítl autorizaci.",
                provider_status=status,
            )
        if status == 429:
            raise ConnectorError(
                ErrorCode.RATE_LIMITED,
                "Poskytovatel dočasně omezuje počet požadavků.",
                retryable=True,
                provider_status=status,
            )
        if status >= 500:
            raise ConnectorError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "Poskytovatel je dočasně nedostupný.",
                retryable=True,
                provider_status=status,
            )
        raise ConnectorError(
            ErrorCode.UPSTREAM_ERROR,
            "Poskytovatel požadavek odmítl.",
            provider_status=status,
        )
