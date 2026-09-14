"""One-use invocation token stores.

The production implementation uses one atomic Valkey ``SET NX EX`` operation.
It deliberately exposes no read operation: a connector only needs to claim a
token ID once, and its ACL can therefore be restricted to a single key prefix.
"""

from __future__ import annotations

import hashlib
import heapq
import logging
import os
import re
import ssl
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import unquote, urlsplit

from valkey import Valkey
from valkey.backoff import NoBackoff
from valkey.exceptions import ValkeyError
from valkey.retry import Retry

logger = logging.getLogger("openmcp.connector")

REPLAY_KEY_PREFIX = "replay:connector:"
_CONNECTOR_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_LOCAL_ENVIRONMENTS = frozenset({"development", "local", "test"})


class ReplayStoreUnavailable(RuntimeError):
    """The one-use decision could not be made safely."""


class ReplayStore(Protocol):
    """Atomic one-use token storage shared by invocation verifiers."""

    def consume(self, jti: str, expires_at: int, now: int) -> bool:
        """Claim ``jti`` once, returning false only when it was already claimed."""

    def ready(self) -> bool:
        """Return whether the store can currently enforce one-use semantics."""

    def close(self) -> None:
        """Release resources held by the store."""


class InMemoryReplayStore:
    """Bounded, thread-safe, single-process store for explicit local/test use."""

    def __init__(self, *, max_entries: int = 10_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: dict[str, int] = {}
        self._expirations: list[tuple[int, str]] = []
        self._lock = threading.Lock()

    def consume(self, jti: str, expires_at: int, now: int) -> bool:
        """Atomically record one JTI, returning false for replay or saturation."""

        with self._lock:
            while self._expirations and self._expirations[0][0] <= now:
                expiry, expired_jti = heapq.heappop(self._expirations)
                if self._entries.get(expired_jti) == expiry:
                    del self._entries[expired_jti]
            if jti in self._entries or len(self._entries) >= self._max_entries:
                return False
            self._entries[jti] = expires_at
            heapq.heappush(self._expirations, (expires_at, jti))
            return True

    def ready(self) -> bool:
        return True

    def close(self) -> None:
        return None


class _ValkeyClient(Protocol):
    def set(
        self, name: str, value: bytes, *, ex: int, nx: bool
    ) -> bool | None: ...

    def ping(self) -> bool: ...

    def close(self) -> None: ...


class ValkeyReplayStore:
    """Distributed replay store backed by an ACL-scoped TLS Valkey client."""

    def __init__(self, client: _ValkeyClient, *, connector: str) -> None:
        if not _CONNECTOR_SLUG.fullmatch(connector):
            raise ValueError("connector must be a canonical lowercase slug")
        self._client = client
        self._key_prefix = f"{REPLAY_KEY_PREFIX}{connector}:v1:"

    def consume(self, jti: str, expires_at: int, now: int) -> bool:
        ttl_seconds = expires_at - now
        if ttl_seconds < 1:
            return False
        # Hashing keeps key size/content fixed even if a future token format
        # permits longer identifiers. It does not weaken the random JTI.
        key = self._key_prefix + hashlib.sha256(jti.encode("ascii")).hexdigest()
        try:
            result = self._client.set(key, b"1", ex=ttl_seconds, nx=True)
        except (ValkeyError, OSError, TimeoutError) as exc:
            raise ReplayStoreUnavailable("replay store unavailable") from exc
        if result is True:
            return True
        if result is None:
            return False
        raise ReplayStoreUnavailable("replay store returned an invalid response")

    def ready(self) -> bool:
        try:
            return self._client.ping() is True
        except (ValkeyError, OSError, TimeoutError):
            return False

    def close(self) -> None:
        try:
            self._client.close()
        except (ValkeyError, OSError, TimeoutError):
            # Shutdown must not expose connection details or mask service cleanup.
            logger.warning("connector_replay_store_close_failed")


def _bounded_integer(
    environ: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _required_secret_file(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    path = Path(value)
    if not value or not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"{name} must reference an existing absolute file")
    return value


def _read_password_file(environ: Mapping[str, str]) -> str:
    name = "OPENMCP_REPLAY_VALKEY_PASSWORD_FILE"
    path = _required_secret_file(environ, name)
    try:
        password = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"{name} could not be read") from exc
    if (
        len(password.encode("utf-8")) < 32
        or len(password.encode("utf-8")) > 1_024
        or any(character in password for character in "\x00\r\n")
    ):
        raise RuntimeError(f"{name} must contain a single strong password")
    return password


def _validate_valkey_url(url: str, connector: str) -> None:
    try:
        parsed = urlsplit(url)
        # Accessing port also rejects malformed/out-of-range values.
        _ = parsed.port
    except ValueError as exc:
        raise RuntimeError("OPENMCP_REPLAY_VALKEY_URL is invalid") from exc
    expected_username = f"replay_{connector.replace('-', '_')}"
    if (
        parsed.scheme != "valkeys"
        or not parsed.hostname
        or parsed.password is not None
        or unquote(parsed.username or "") != expected_username
        or parsed.path not in {"", "/", "/0"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "OPENMCP_REPLAY_VALKEY_URL must be a password-free valkeys:// "
            f"{expected_username} ACL URL for DB 0"
        )


def replay_store_from_env(
    connector: str, *, environ: Mapping[str, str] | None = None
) -> ReplayStore:
    """Build the explicitly selected replay store from process configuration."""

    values = os.environ if environ is None else environ
    mode = values.get("OPENMCP_REPLAY_STORE", "").strip().lower()
    if mode == "memory":
        environment = values.get("APP_ENV", "").strip().lower()
        if environment not in _LOCAL_ENVIRONMENTS:
            raise RuntimeError(
                "OPENMCP_REPLAY_STORE=memory is allowed only in local/development/test"
            )
        max_entries = _bounded_integer(
            values,
            "OPENMCP_REPLAY_MEMORY_MAX_ENTRIES",
            default=10_000,
            minimum=1,
            maximum=100_000,
        )
        logger.warning("connector_replay_store_process_local")
        return InMemoryReplayStore(max_entries=max_entries)
    if mode != "valkey":
        raise RuntimeError("OPENMCP_REPLAY_STORE must be explicitly set to valkey or memory")

    url = values.get("OPENMCP_REPLAY_VALKEY_URL", "")
    _validate_valkey_url(url, connector)
    password = _read_password_file(values)
    ca_file = _required_secret_file(values, "OPENMCP_REPLAY_TLS_CA_FILE")
    cert_file = _required_secret_file(values, "OPENMCP_REPLAY_TLS_CERT_FILE")
    key_file = _required_secret_file(values, "OPENMCP_REPLAY_TLS_KEY_FILE")
    connect_timeout_ms = _bounded_integer(
        values,
        "OPENMCP_REPLAY_CONNECT_TIMEOUT_MS",
        default=100,
        minimum=10,
        maximum=1_000,
    )
    socket_timeout_ms = _bounded_integer(
        values,
        "OPENMCP_REPLAY_SOCKET_TIMEOUT_MS",
        default=100,
        minimum=10,
        maximum=1_000,
    )
    max_connections = _bounded_integer(
        values,
        "OPENMCP_REPLAY_MAX_CONNECTIONS",
        default=16,
        minimum=1,
        maximum=64,
    )

    client = Valkey.from_url(
        url,
        password=password,
        protocol=3,
        encoding="utf-8",
        decode_responses=False,
        socket_connect_timeout=connect_timeout_ms / 1_000,
        socket_timeout=socket_timeout_ms / 1_000,
        socket_keepalive=True,
        health_check_interval=30,
        max_connections=max_connections,
        client_name=f"openmcp-connector-{connector}-replay",
        retry=Retry(NoBackoff(), 0),
        ssl_ca_certs=ca_file,
        ssl_certfile=cert_file,
        ssl_keyfile=key_file,
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        ssl_min_version=ssl.TLSVersion.TLSv1_2,
    )
    return ValkeyReplayStore(cast(_ValkeyClient, client), connector=connector)
