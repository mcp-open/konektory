from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from openmcp_connector_runtime.replay import (
    REPLAY_KEY_PREFIX,
    InMemoryReplayStore,
    ReplayStoreUnavailable,
    ValkeyReplayStore,
    replay_store_from_env,
)


class FakeValkeyClient:
    def __init__(self, *, set_result: bool | None = True) -> None:
        self.set_result = set_result
        self.set_calls: list[tuple[str, bytes, int, bool]] = []
        self.ping_result = True
        self.raise_connection_error = False
        self.closed = False

    def set(self, name: str, value: bytes, *, ex: int, nx: bool) -> bool | None:
        if self.raise_connection_error:
            raise ValkeyConnectionError("must not escape")
        self.set_calls.append((name, value, ex, nx))
        return self.set_result

    def ping(self) -> bool:
        if self.raise_connection_error:
            raise ValkeyConnectionError("must not escape")
        return self.ping_result

    def close(self) -> None:
        self.closed = True


class AtomicFakeValkeyClient(FakeValkeyClient):
    def __init__(self) -> None:
        super().__init__()
        self._keys: set[str] = set()
        self._lock = threading.Lock()

    def set(self, name: str, value: bytes, *, ex: int, nx: bool) -> bool | None:
        with self._lock:
            if name in self._keys:
                return None
            self._keys.add(name)
            return True


def test_valkey_store_claims_hashed_scoped_key_atomically_with_token_ttl() -> None:
    client = FakeValkeyClient()
    store = ValkeyReplayStore(client, connector="ares")

    assert store.consume("random-token-id", expires_at=1_060, now=1_000) is True
    expected_key = (
        f"{REPLAY_KEY_PREFIX}ares:v1:"
        + hashlib.sha256(b"random-token-id").hexdigest()
    )
    assert client.set_calls == [(expected_key, b"1", 60, True)]


def test_valkey_store_distinguishes_replay_and_unavailable_store() -> None:
    replay_client = FakeValkeyClient(set_result=None)
    assert (
        ValkeyReplayStore(replay_client, connector="ares").consume(
            "random-token-id", expires_at=1_060, now=1_000
        )
        is False
    )

    unavailable_client = FakeValkeyClient()
    unavailable_client.raise_connection_error = True
    store = ValkeyReplayStore(unavailable_client, connector="ares")
    with pytest.raises(ReplayStoreUnavailable, match="replay store unavailable"):
        store.consume("random-token-id", expires_at=1_060, now=1_000)
    assert store.ready() is False


def test_two_connector_replicas_share_one_atomic_claim() -> None:
    client = AtomicFakeValkeyClient()
    replicas = [
        ValkeyReplayStore(client, connector="ares"),
        ValkeyReplayStore(client, connector="ares"),
    ]

    def consume(index: int) -> bool:
        return replicas[index % 2].consume(
            "same-token-on-every-replica", expires_at=1_060, now=1_000
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(consume, range(64)))
    assert results.count(True) == 1
    assert results.count(False) == 63


def test_different_connectors_cannot_collide_on_the_same_jti() -> None:
    client = AtomicFakeValkeyClient()
    ares = ValkeyReplayStore(client, connector="ares")
    dotykacka = ValkeyReplayStore(client, connector="dotykacka")

    assert ares.consume("same-random-jti", expires_at=1_060, now=1_000) is True
    assert dotykacka.consume("same-random-jti", expires_at=1_060, now=1_000) is True


def test_valkey_store_rejects_invalid_connector_and_unexpected_response() -> None:
    with pytest.raises(ValueError, match="canonical lowercase slug"):
        ValkeyReplayStore(FakeValkeyClient(), connector="../../other")
    store = ValkeyReplayStore(FakeValkeyClient(set_result=False), connector="ares")
    with pytest.raises(ReplayStoreUnavailable, match="invalid response"):
        store.consume("random-token-id", expires_at=1_060, now=1_000)


def test_memory_store_requires_explicit_non_production_environment() -> None:
    with pytest.raises(RuntimeError, match="explicitly set"):
        replay_store_from_env("ares", environ={})
    with pytest.raises(RuntimeError, match="allowed only"):
        replay_store_from_env(
            "ares", environ={"OPENMCP_REPLAY_STORE": "memory", "APP_ENV": "production"}
        )

    store = replay_store_from_env(
        "ares",
        environ={
            "OPENMCP_REPLAY_STORE": "memory",
            "APP_ENV": "test",
            "OPENMCP_REPLAY_MEMORY_MAX_ENTRIES": "1",
        },
    )
    assert isinstance(store, InMemoryReplayStore)
    assert store.consume("first", 20, 10) is True
    assert store.consume("second", 20, 10) is False


@pytest.mark.parametrize(
    "url",
    [
        "valkey://replay_ares@valkey:6379/0",
        "valkeys://default@valkey:6379/0",
        "valkeys://replay_dotykacka@valkey:6379/0",
        "valkeys://replay_ares@valkey:6379/1",
        "valkeys://replay_ares@valkey:6379/0?ssl_check_hostname=false",
        "valkeys://replay_ares:secret@valkey:6379/0",
    ],
)
def test_valkey_configuration_rejects_insecure_or_wrong_identity_urls(
    url: str, tmp_path: Path
) -> None:
    ca_file = tmp_path / "ca.pem"
    cert_file = tmp_path / "tls.crt"
    key_file = tmp_path / "tls.key"
    for path in (ca_file, cert_file, key_file):
        path.write_text("fixture")
    password_file = tmp_path / "password"
    password_file.write_text("a-strong-replay-password-for-tests")
    with pytest.raises(RuntimeError, match="password-free valkeys://"):
        replay_store_from_env(
            "ares",
            environ={
                "OPENMCP_REPLAY_STORE": "valkey",
                "OPENMCP_REPLAY_VALKEY_URL": url,
                "OPENMCP_REPLAY_VALKEY_PASSWORD_FILE": str(password_file),
                "OPENMCP_REPLAY_TLS_CA_FILE": str(ca_file),
                "OPENMCP_REPLAY_TLS_CERT_FILE": str(cert_file),
                "OPENMCP_REPLAY_TLS_KEY_FILE": str(key_file),
            },
        )


def test_valkey_configuration_requires_all_mtls_files(tmp_path: Path) -> None:
    ca_file = tmp_path / "ca.pem"
    password_file = tmp_path / "password"
    ca_file.write_text("fixture")
    password_file.write_text("a-strong-replay-password-for-tests")
    with pytest.raises(RuntimeError, match="CERT_FILE"):
        replay_store_from_env(
            "ares",
            environ={
                "OPENMCP_REPLAY_STORE": "valkey",
                "OPENMCP_REPLAY_VALKEY_URL": (
                    "valkeys://replay_ares@valkey:6379/0"
                ),
                "OPENMCP_REPLAY_VALKEY_PASSWORD_FILE": str(password_file),
                "OPENMCP_REPLAY_TLS_CA_FILE": str(ca_file),
            },
        )


def test_valkey_factory_enforces_bounded_tls_client_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ca_file = tmp_path / "ca.pem"
    cert_file = tmp_path / "tls.crt"
    key_file = tmp_path / "tls.key"
    password_file = tmp_path / "password"
    for path in (ca_file, cert_file, key_file):
        path.write_text("fixture")
    password_file.write_text("a-strong-replay-password-for-tests")
    fake_client = FakeValkeyClient()
    captured: dict[str, Any] = {}

    def fake_from_url(url: str, **kwargs: Any) -> FakeValkeyClient:
        captured["url"] = url
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        "openmcp_connector_runtime.replay.Valkey.from_url", staticmethod(fake_from_url)
    )
    url = "valkeys://replay_ares@valkey:6379/0"
    store = replay_store_from_env(
        "ares",
        environ={
            "OPENMCP_REPLAY_STORE": "valkey",
            "OPENMCP_REPLAY_VALKEY_URL": url,
            "OPENMCP_REPLAY_VALKEY_PASSWORD_FILE": str(password_file),
            "OPENMCP_REPLAY_TLS_CA_FILE": str(ca_file),
            "OPENMCP_REPLAY_TLS_CERT_FILE": str(cert_file),
            "OPENMCP_REPLAY_TLS_KEY_FILE": str(key_file),
            "OPENMCP_REPLAY_CONNECT_TIMEOUT_MS": "75",
            "OPENMCP_REPLAY_SOCKET_TIMEOUT_MS": "125",
            "OPENMCP_REPLAY_MAX_CONNECTIONS": "8",
        },
    )

    assert isinstance(store, ValkeyReplayStore)
    assert captured["url"] == url
    assert captured["password"] == "a-strong-replay-password-for-tests"
    assert captured["socket_connect_timeout"] == 0.075
    assert captured["socket_timeout"] == 0.125
    assert captured["max_connections"] == 8
    assert captured["ssl_ca_certs"] == str(ca_file)
    assert captured["ssl_certfile"] == str(cert_file)
    assert captured["ssl_keyfile"] == str(key_file)
    assert captured["ssl_cert_reqs"] == "required"
    assert captured["ssl_check_hostname"] is True
    assert store.ready() is True
    store.close()
    assert fake_client.closed is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OPENMCP_REPLAY_CONNECT_TIMEOUT_MS", "9"),
        ("OPENMCP_REPLAY_SOCKET_TIMEOUT_MS", "1001"),
        ("OPENMCP_REPLAY_MAX_CONNECTIONS", "65"),
    ],
)
def test_valkey_factory_rejects_unbounded_resource_configuration(
    name: str, value: str, tmp_path: Path
) -> None:
    files = [tmp_path / filename for filename in ("ca.pem", "tls.crt", "tls.key")]
    password_file = tmp_path / "password"
    for path in files:
        path.write_text("fixture")
    password_file.write_text("a-strong-replay-password-for-tests")
    environ = {
        "OPENMCP_REPLAY_STORE": "valkey",
        "OPENMCP_REPLAY_VALKEY_URL": (
            "valkeys://replay_ares@valkey:6379/0"
        ),
        "OPENMCP_REPLAY_VALKEY_PASSWORD_FILE": str(password_file),
        "OPENMCP_REPLAY_TLS_CA_FILE": str(files[0]),
        "OPENMCP_REPLAY_TLS_CERT_FILE": str(files[1]),
        "OPENMCP_REPLAY_TLS_KEY_FILE": str(files[2]),
        name: value,
    }
    with pytest.raises(RuntimeError, match="must be between"):
        replay_store_from_env("ares", environ=environ)
