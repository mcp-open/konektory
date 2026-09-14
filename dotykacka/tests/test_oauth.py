from __future__ import annotations

import asyncio

import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode

from connector_dotykacka.oauth import DotykackaOAuth


class FakeUpstream:
    def __init__(self) -> None:
        self.calls = 0

    async def request_json(self, *_args, **_kwargs):
        self.calls += 1
        return {"accessToken": f"access-{self.calls}", "expiresIn": 120}, "https://provider"


@pytest.mark.anyio
async def test_oauth_cache_and_per_key_locks_stay_bounded() -> None:
    upstream = FakeUpstream()
    oauth = DotykackaOAuth(upstream, max_entries=3)

    for version in range(1, 21):
        await oauth.access_token(f"refresh-{version}", "cloud-1", version)

    assert len(oauth._tokens) == 3
    assert len(oauth._locks) == 0


@pytest.mark.anyio
async def test_expired_entries_are_purged_before_new_insert() -> None:
    now = [100.0]
    upstream = FakeUpstream()
    oauth = DotykackaOAuth(upstream, max_entries=10, clock=lambda: now[0])
    await oauth.access_token("refresh-1", "cloud-1", 1)
    assert len(oauth._tokens) == 1

    now[0] += 61
    await oauth.access_token("refresh-2", "cloud-1", 2)

    assert len(oauth._tokens) == 1
    assert len(oauth._locks) == 0


@pytest.mark.anyio
async def test_same_refresh_token_is_exchanged_once_under_concurrency() -> None:
    upstream = FakeUpstream()
    oauth = DotykackaOAuth(upstream)

    results = await asyncio.gather(
        *[oauth.access_token("refresh", "cloud-1", 1) for _ in range(32)]
    )

    assert upstream.calls == 1
    assert {token for token, _ in results} == {"access-1"}
    assert len(oauth._locks) == 0


@pytest.mark.anyio
async def test_token_ttl_starts_after_slow_exchange() -> None:
    now = [100.0]

    class SlowUpstream(FakeUpstream):
        async def request_json(self, *_args, **_kwargs):
            self.calls += 1
            now[0] += 30
            return {"accessToken": "access-slow", "expiresIn": 120}, "https://provider"

    oauth = DotykackaOAuth(SlowUpstream(), clock=lambda: now[0])
    token, key = await oauth.access_token("refresh", "cloud-1", 1)

    assert token == "access-slow"
    assert oauth._tokens[key].expires_at == 190.0


@pytest.mark.anyio
async def test_malformed_oauth_payload_cannot_echo_provider_credentials() -> None:
    secret = "refresh-never-log"

    class LeakyPayloadUpstream:
        async def request_json(self, *_args, **_kwargs):
            return {"error": secret, "accessToken": ""}, "https://provider"

    oauth = DotykackaOAuth(LeakyPayloadUpstream())
    with pytest.raises(ConnectorError) as caught:
        await oauth.access_token(secret, "cloud-1", 1)

    assert caught.value.code == ErrorCode.UPSTREAM_ERROR
    assert secret not in str(caught.value)
