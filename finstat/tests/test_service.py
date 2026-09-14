from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_finstat.app import build_definition
from connector_finstat.service import ENDPOINTS, FinstatService, verification_hash

API_KEY = "synthetic-api-key-0123456789"
PRIVATE_KEY = "synthetic-private-key-0123456789"


def context(**updates: Any) -> InvocationContext:
    values = {
        "api_key": API_KEY,
        "private_key": PRIVATE_KEY,
        "pii_key": "synthetic-pii-key-0123456789abcdef",
    }
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="finstat/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


def test_verification_hash_matches_official_clients() -> None:
    expected = hashlib.sha256(b"SomeSalt+k+p++35757442+ended").hexdigest()
    assert verification_hash("k", "p", "35757442") == expected


@pytest.mark.anyio
async def test_all_tools_post_form_fields_to_documented_json_endpoints() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"Ico": "35757442", "Name": "Private Company s.r.o."})

    definition = build_definition(FinstatService(transport=httpx.MockTransport(upstream)))
    cases = {
        "get_basic": ({"ico": "35757442"}, "/api/basic.json", "35757442"),
        "get_detail": ({"ico": "35757442"}, "/api/detail.json", "35757442"),
        "get_extended": ({"ico": "35757442"}, "/api/extended.json", "35757442"),
        "get_ultimate": ({"ico": "35757442"}, "/api/ultimate.json", "35757442"),
        "autocomplete": ({"query": "finstat"}, "/api/autocomplete.json", "finstat"),
        "list_statements": ({"ico": "35757442"}, "/api/GetStatements.json", "35757442"),
        "get_statement": (
            {"ico": "35757442", "year": 2023, "template": "Template2014"},
            "/api/GetStatementDetail.json",
            "35757442|2023",
        ),
    }
    assert set(definition.tools) == set(cases)
    for name, (arguments, path, parameter) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only
        assert request.method == "POST"
        assert request.url.host == "www.finstat.sk" and request.url.path == path
        assert path.removeprefix("/api") in ENDPOINTS
        assert not request.url.query
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert request.headers["user-agent"].startswith("OpenMCP/")
        fields = parse_qs(request.content.decode(), strict_parsing=True)
        assert fields["apiKey"] == [API_KEY]
        assert fields["Hash"] == [verification_hash(API_KEY, PRIVATE_KEY, parameter)]
        assert fields["StationId"] == ["openmcp"]
        assert PRIVATE_KEY not in request.content.decode()
        assert "Private Company" not in result.model_dump_json()
        assert API_KEY not in result.model_dump_json()
    fields = parse_qs(seen[-1].content.decode())
    assert fields["ico"] == ["35757442"]
    assert fields["year"] == ["2023"]
    assert fields["template"] == ["Template2014"]
    assert all(request.method == "POST" for request in seen)


@pytest.mark.anyio
async def test_bad_credentials_and_provider_errors_fail_closed() -> None:
    calls = 0
    reply: dict[str, Any] = {"status": 403, "body": b"Invalid verification hash! private-detail"}

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(reply["status"], content=reply["body"])

    service = FinstatService(transport=httpx.MockTransport(upstream))
    for bad in ({"api_key": "short"}, {"private_key": "with space and more"}):
        with pytest.raises(ConnectorError) as caught:
            await service.test_connection(context(credentials=bad))
        assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert calls == 0
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert "private-detail" not in str(caught.value)
    for status in (402, 404, 429, 503):
        reply["status"] = status
        with pytest.raises(ConnectorError):
            await service.test_connection(context())
    reply.update(status=200, body=b"<xml>not json</xml>")
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR
    reply["body"] = b'"just a string"'
    with pytest.raises(ConnectorError):
        await service.test_connection(context())


@pytest.mark.parametrize(
    "arguments",
    [
        {"ico": "1234567"},
        {"ico": "123456789"},
        {"ico": "3575744a"},
        {"ico": 35757442},
        {"ico": ""},
        {"ico": "35757442", "url": "https://evil"},
        [],
    ],
)
def test_company_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools["get_detail"].input_model.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {"ico": "35757442", "year": 1900, "template": "Template2014"},
        {"ico": "35757442", "year": "2023", "template": "Template2014"},
        {"ico": "35757442", "year": 2023, "template": "Other"},
        {"ico": "35757442", "year": 2023},
        {"query": "a"},
    ],
)
def test_statement_and_autocomplete_arguments_are_bounded(arguments: Any) -> None:
    tool = "autocomplete" if "query" in arguments else "get_statement"
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
