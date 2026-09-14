from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext

from connector_ares.schemas import LookupInput, SearchInput
from connector_ares.service import AresService, ico_checksum

FIXTURES = Path(__file__).parent / "fixtures"


def context() -> InvocationContext:
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
    )


@pytest.mark.anyio
async def test_lookup_returns_reduced_envelope_and_provenance() -> None:
    payload = json.loads((FIXTURES / "lookup.json").read_text())

    async def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/ekonomicke-subjekty/27074358")
        return httpx.Response(200, json=payload)

    service = AresService(transport=httpx.MockTransport(responder))
    try:
        result = await service.lookup(LookupInput(ico="27074358"), context())
    finally:
        await service.close()
    assert result.data["obchodni_jmeno"] == "OpenMCP Test s.r.o."
    assert result.data["registrace"] == ["res", "vr"]
    assert result.provenance.source_url.endswith("/ekonomicke-subjekty/27074358")


@pytest.mark.anyio
async def test_invalid_ico_is_rejected_before_upstream() -> None:
    service = AresService(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    try:
        with pytest.raises(ConnectorError) as error:
            await service.lookup(LookupInput(ico="12345678"), context())
    finally:
        await service.close()
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert ico_checksum("27074358")


@pytest.mark.anyio
async def test_search_is_bounded_and_reports_truncation() -> None:
    payload = json.loads((FIXTURES / "search.json").read_text())

    async def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"obchodniJmeno": "OpenMCP", "start": 0, "pocet": 1}
        return httpx.Response(200, json=payload)

    service = AresService(transport=httpx.MockTransport(responder))
    try:
        result = await service.search(SearchInput(obchodni_jmeno="OpenMCP", pocet=1), context())
    finally:
        await service.close()
    assert result.data["pocet_celkem"] == 2
    assert result.warnings


@pytest.mark.anyio
async def test_provider_body_is_not_leaked_from_error() -> None:
    async def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="private provider diagnostics")

    service = AresService(transport=httpx.MockTransport(responder))
    try:
        with pytest.raises(ConnectorError) as error:
            await service.lookup(LookupInput(ico="27074358"), context())
    finally:
        await service.close()
    assert error.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert "private" not in error.value.message

