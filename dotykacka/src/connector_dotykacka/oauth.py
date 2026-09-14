"""Serialized, bounded Dotykačka refresh-token exchange and access-token cache."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openmcp_connector_runtime import ConnectorError, ErrorCode, UpstreamClient

DEFAULT_TTL = 3300
LEEWAY = 60


@dataclass
class _CachedToken:
    value: str
    expires_at: float
    last_used: float


@dataclass
class _KeyLock:
    lock: asyncio.Lock
    users: int = 0


class DotykackaOAuth:
    def __init__(
        self,
        client: UpstreamClient,
        *,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_entries <= 10_000:
            raise ValueError("max_entries must be between 1 and 10000")
        self._client = client
        self._max_entries = max_entries
        self._clock = clock
        self._tokens: dict[str, _CachedToken] = {}
        self._locks: dict[str, _KeyLock] = {}
        self._guard = asyncio.Lock()

    @staticmethod
    def _key(refresh_token: str, cloud_id: str, secret_version: int) -> str:
        return hashlib.sha256(
            f"{cloud_id}\0{secret_version}\0{refresh_token}".encode()
        ).hexdigest()

    async def access_token(
        self, refresh_token: str, cloud_id: str, secret_version: int
    ) -> tuple[str, str]:
        key = self._key(refresh_token, cloud_id, secret_version)
        now = self._clock()
        cached = self._tokens.get(key)
        if cached is not None and now < cached.expires_at:
            cached.last_used = now
            return cached.value, key
        async with self._guard:
            self._purge_expired(now)
            entry = self._locks.get(key)
            if entry is None:
                entry = _KeyLock(asyncio.Lock())
                self._locks[key] = entry
            entry.users += 1
        try:
            async with entry.lock:
                now = self._clock()
                cached = self._tokens.get(key)
                if cached is not None and now < cached.expires_at:
                    cached.last_used = now
                    return cached.value, key
                payload, _ = await self._client.request_json(
                    "POST",
                    "/signin/token",
                    headers={"Authorization": f"User {refresh_token}"},
                    json_body={"_cloudId": cloud_id},
                )
                if not isinstance(payload, dict):
                    raise ConnectorError(
                        ErrorCode.UPSTREAM_ERROR, "Dotykačka vrátila neplatnou OAuth odpověď."
                    )
                token = payload.get("accessToken")
                if not isinstance(token, str) or not token:
                    raise ConnectorError(
                        ErrorCode.UPSTREAM_ERROR, "Dotykačka nevydala přístupový token."
                    )
                raw_ttl: Any = payload.get("expiresIn", DEFAULT_TTL)
                ttl = (
                    raw_ttl
                    if isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool)
                    else DEFAULT_TTL
                )
                ttl = max(60, min(ttl, 24 * 60 * 60))
                issued_at = self._clock()
                async with self._guard:
                    self._purge_expired(issued_at)
                    if key not in self._tokens and len(self._tokens) >= self._max_entries:
                        oldest = min(self._tokens, key=lambda item: self._tokens[item].last_used)
                        self._tokens.pop(oldest, None)
                        oldest_lock = self._locks.get(oldest)
                        if oldest_lock is not None and oldest_lock.users == 0:
                            self._locks.pop(oldest, None)
                    self._tokens[key] = _CachedToken(
                        token, issued_at + max(ttl - LEEWAY, 0), issued_at
                    )
                return token, key
        finally:
            async with self._guard:
                current_lock = self._locks.get(key)
                if current_lock is entry:
                    entry.users -= 1
                if current_lock is entry and entry.users == 0:
                    self._locks.pop(key, None)

    def invalidate(self, cache_key: str) -> None:
        self._tokens.pop(cache_key, None)
        lock = self._locks.get(cache_key)
        if lock is not None and lock.users == 0:
            self._locks.pop(cache_key, None)

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, token in self._tokens.items() if now >= token.expires_at]
        for key in expired:
            self._tokens.pop(key, None)
            lock = self._locks.get(key)
            if lock is not None and lock.users == 0:
                self._locks.pop(key, None)
