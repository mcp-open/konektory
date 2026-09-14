from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import re
import time
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_websupport.app import build_definition
from connector_websupport.service import WebsupportService, signature, signed_headers

API_KEY = "ak48l3h7-ak5d-qn4t-p8gc-synthetic"
API_SECRET = "synthetic-secret-0123456789abcdef"


def context(**updates: Any) -> InvocationContext:
    values = {
        "api_key": API_KEY,
        "api_secret": API_SECRET,
        "pii_key": "synthetic-pii-key-0123456789abcdef",
    }
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="websupport/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


def test_signature_matches_documented_python_example() -> None:
    canonical = b"GET /v2/service/1/dns/record 1548240417"
    expected = hmac.new(b"s", canonical, hashlib.sha1).hexdigest()
    assert signature("s", "GET", "/v2/service/1/dns/record", 1548240417) == expected
    headers = signed_headers("k", "s", "GET", "/v2/service/1/dns/record", 1548240417)
    assert headers["X-Date"] == headers["Date"] == "20190123T104657Z"
    assert base64.b64decode(headers["Authorization"][6:]).decode() == f"k:{expected}"


@pytest.mark.anyio
async def test_all_tools_use_documented_get_paths_with_hmac_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [{"id": 1, "name": "private-domain.sk"}]})

    definition = build_definition(WebsupportService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_services": ({"page": 2, "pagesize": 20}, "/v1/user/self/service"),
        "get_service": ({"service_id": 1111}, "/v1/user/self/service/1111"),
        "list_zones": ({}, "/v1/user/self/zone"),
        "get_dns_zone": ({"service_id": 1111}, "/v2/service/1111/dns/zone"),
        "list_dns_records": (
            {"service_id": 1111, "name": "www", "rows_per_page": 10},
            "/v2/service/1111/dns/record",
        ),
        "list_ftp_accounts": ({"service_id": 1111}, "/v2/service/1111/ftp-account"),
        "get_ftp_account": (
            {"service_id": 1111, "ftp_account_id": 7},
            "/v2/service/1111/ftp-account/7",
        ),
    }
    assert set(definition.tools) == set(cases)
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.host == "rest.websupport.sk" and request.url.path == path
        assert request.headers["user-agent"].startswith("OpenMCP/")
        assert request.headers["accept"] == "application/json"
        stamp = request.headers["x-date"]
        assert re.fullmatch(r"\d{8}T\d{6}Z", stamp) and request.headers["date"] == stamp
        key, _, sig = base64.b64decode(request.headers["authorization"][6:]).decode().partition(":")
        assert key == API_KEY and re.fullmatch(r"[0-9a-f]{40}", sig)
        assert API_SECRET not in str(request.headers) and API_SECRET not in str(request.url)
        text = result.model_dump_json()
        assert "private-domain" not in text and API_KEY not in text
    assert seen[0].url.params["page"] == "2" and seen[0].url.params["pagesize"] == "20"
    assert seen[2].url.params["pagesize"] == "50"
    assert seen[4].url.params["rowsPerPage"] == "10"
    assert seen[4].url.params["filters[name]"] == "www"
    assert "filters[content]" not in seen[4].url.params
    assert seen[5].url.params["rowsPerPage"] == "50"


@pytest.mark.anyio
async def test_signature_covers_path_and_current_timestamp() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"verified": True})

    service = WebsupportService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    request = seen[-1]
    assert request.url.path == "/v2/check"
    stamp = request.headers["x-date"]
    timestamp = calendar.timegm(time.strptime(stamp, "%Y%m%dT%H%M%SZ"))
    _, _, sig = base64.b64decode(request.headers["authorization"][6:]).decode().partition(":")
    assert sig == signature(API_SECRET, "GET", "/v2/check", timestamp)
    assert abs(timestamp - int(time.time())) < 60


@pytest.mark.anyio
async def test_bad_credentials_and_provider_errors_fail_closed() -> None:
    calls = 0
    reply: dict[str, Any] = {"status": 200, "json": {"verified": False}}

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(reply["status"], json=reply["json"])

    service = WebsupportService(transport=httpx.MockTransport(upstream))
    for bad in ({"api_key": "short"}, {"api_secret": "with space 0123456789"}):
        with pytest.raises(ConnectorError) as caught:
            await service.test_connection(context(credentials=bad))
        assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert calls == 0
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    reply["json"] = {"code": 400, "message": "Private validation message"}
    with pytest.raises(ConnectorError) as caught:
        await service.get(context(), "/v1/user/self/zone")
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR
    assert "Private validation" not in str(caught.value)
    for status, code in (
        (401, ErrorCode.CREDENTIAL_INVALID),
        (404, ErrorCode.NOT_FOUND),
        (429, ErrorCode.RATE_LIMITED),
        (500, ErrorCode.UPSTREAM_UNAVAILABLE),
    ):
        reply["status"] = status
        with pytest.raises(ConnectorError) as caught:
            await service.get(context(), "/v1/user/self/zone")
        assert caught.value.code is code
    reply.update(status=200, json=["not", "an", "object"])
    with pytest.raises(ConnectorError):
        await service.get(context(), "/v1/user/self/zone")
    with pytest.raises(ConnectorError):
        await service.get(context(), "/v3/anything")


@pytest.mark.parametrize(
    "arguments",
    [
        {"service_id": 0},
        {"service_id": "1111"},
        {"service_id": True},
        {"service_id": 1111, "page": 0},
        {"service_id": 1111, "rows_per_page": 201},
        {"service_id": 1111, "name": ""},
        {"service_id": 1111, "type": "A"},
        {"service_id": 1111, "url": "https://evil"},
        [],
    ],
)
def test_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools["list_dns_records"].input_model.model_validate(arguments)
