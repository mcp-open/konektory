from __future__ import annotations

import asyncio
import gzip

import httpx
import pytest

from openmcp_connector_runtime import ConnectorError, ErrorCode
from openmcp_connector_runtime.upstream import UpstreamClient


@pytest.mark.anyio
async def test_environment_cannot_replace_provider_tls_trust_or_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent-untrusted-provider-ca.pem")
    monkeypatch.setenv("HTTPS_PROXY", "not-a-valid-proxy-url")
    client = UpstreamClient("https://provider.example.test")
    await client.close()


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@provider.example",
        "https://provider.example?credential=secret",
        "https://provider.example/#fragment",
    ],
)
def test_rejects_upstream_origins_with_credentials_or_url_metadata(base_url: str) -> None:
    with pytest.raises(ValueError, match="fixed HTTPS origin"):
        UpstreamClient(base_url)


@pytest.mark.anyio
async def test_rejects_declared_oversized_response_before_reading_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "1025"},
            content=b'{}',
            request=request,
        )

    client = UpstreamClient(
        "https://provider.example",
        max_response_bytes=1024,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ConnectorError) as caught:
            await client.request_json("GET", "/resource")
    finally:
        await client.close()

    assert caught.value.code == ErrorCode.UPSTREAM_ERROR


@pytest.mark.anyio
async def test_rejects_streamed_oversized_response_without_content_length() -> None:
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"value":"'
            yield b"x" * 1024
            yield b'"}'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OversizedStream(), request=request)

    client = UpstreamClient(
        "https://provider.example",
        max_response_bytes=1024,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ConnectorError) as caught:
            await client.request_json("GET", "/resource")
    finally:
        await client.close()

    assert caught.value.code == ErrorCode.UPSTREAM_ERROR


@pytest.mark.anyio
async def test_response_limit_applies_after_gzip_decompression() -> None:
    compressed = gzip.compress(b"x" * 2048)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(compressed))},
            content=compressed,
            request=request,
        )

    client = UpstreamClient(
        "https://provider.example",
        max_response_bytes=1024,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ConnectorError) as caught:
            await client.request_json("GET", "/resource")
    finally:
        await client.close()

    assert caught.value.code == ErrorCode.UPSTREAM_ERROR


@pytest.mark.anyio
async def test_parses_response_at_exact_limit_and_scrubs_source_query() -> None:
    body = b'{"ok":true}'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, request=request)

    client = UpstreamClient(
        "https://provider.example",
        max_response_bytes=len(body),
        transport=httpx.MockTransport(handler),
    )
    try:
        payload, source = await client.request_json(
            "GET", "/resource", params={"secret": "not-in-provenance"}
        )
    finally:
        await client.close()

    assert payload == {"ok": True}
    assert source == "https://provider.example/resource"


@pytest.mark.anyio
async def test_maps_excessively_nested_json_to_safe_upstream_error() -> None:
    body = b"[" * 2000 + b"0" + b"]" * 2000

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, request=request)

    client = UpstreamClient(
        "https://provider.example",
        max_response_bytes=len(body),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ConnectorError) as caught:
            await client.request_json("GET", "/resource")
    finally:
        await client.close()

    assert caught.value.code == ErrorCode.UPSTREAM_ERROR


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    ["//attacker.example/private", "/\\\\attacker.example/private", "/safe/%2e%2e/private"],
)
async def test_rejects_paths_that_could_escape_the_fixed_upstream_origin(path: str) -> None:
    called = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = UpstreamClient(
        "https://provider.example", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ConnectorError) as caught:
            await client.request_json("GET", path)
    finally:
        await client.close()

    assert caught.value.code == ErrorCode.INTERNAL
    assert called is False


@pytest.mark.anyio
async def test_does_not_accept_redirect_response_as_provider_data() -> None:
    requests: list[httpx.URL] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        return httpx.Response(
            302,
            headers={"Location": "https://attacker.example/private"},
            json={"redirected": True},
            request=request,
        )

    client = UpstreamClient(
        "https://provider.example", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ConnectorError) as caught:
            await client.request_json("GET", "/resource")
    finally:
        await client.close()

    assert caught.value.code == ErrorCode.UPSTREAM_ERROR
    assert requests == [httpx.URL("https://provider.example/resource")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw", [b'{"value":NaN}', b'{"value":Infinity}', b'{"value":"\\ud800"}']
)
async def test_rejects_non_standard_json_and_unpaired_unicode(raw: bytes) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, request=request)

    client = UpstreamClient(
        "https://provider.example", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ConnectorError) as caught:
            await client.request_json("GET", "/resource")
    finally:
        await client.close()

    assert caught.value.code == ErrorCode.UPSTREAM_ERROR


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["NaN", "1e309", "-1e309", "garbage"])
async def test_retry_after_is_always_finite_and_bounded(raw: str) -> None:
    response = httpx.Response(429, headers={"Retry-After": raw})
    delay = UpstreamClient._retry_delay(response, attempt=0)
    assert 0.0 <= delay <= 0.5


@pytest.mark.anyio
async def test_request_cancellation_propagates_without_retrying() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return httpx.Response(200, json={}, request=request)

    client = UpstreamClient(
        "https://provider.example",
        max_attempts=3,
        transport=httpx.MockTransport(handler),
    )
    task = asyncio.create_task(client.request_json("GET", "/resource"))
    await started.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await client.close()

    assert calls == 1
