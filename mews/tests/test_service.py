from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_mews.app import build_definition
from connector_mews.service import ALLOWED_PATHS, CLIENT, SLUG, MewsService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
CLIENT_TOKEN = "E0D439EE522F44368DC78E1BFB03710C-SYNTHETICCLIENT"
ACCESS_TOKEN = "C66EF7B239D24632943D115EDE9CB810-SYNTHETICACCESS"
GUID = "0f515589-99b4-423d-b83a-b237009f0509"
CURSOR = "7f9325f6-ef44-4911-89a8-ae51010a5aa4"


def context(**changes: Any) -> InvocationContext:
    values = {
        "client_token": CLIENT_TOKEN,
        "access_token": ACCESS_TOKEN,
        "environment": "production",
        "pii_key": PII_KEY,
    }
    values.update(changes.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="subject-1",
        workspace_id="workspace-1",
        installation_id="install-1",
        secret_ref=f"{SLUG}/workspace-1/install-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=changes)


CASES: dict[str, tuple[dict[str, Any], str, str | None]] = {
    "list_reservations": (
        {
            "start": "2024-01-01",
            "end": "2024-03-31T23:59:59Z",
            "window": "colliding",
            "states": ["Confirmed", "Started"],
            "service_ids": [GUID],
            "count": 50,
            "cursor": CURSOR,
        },
        "/api/connector/v1/reservations/getAll/2023-06-06",
        "Reservations",
    ),
    "list_customers": (
        {"start": "2024-01-01", "end": "2024-01-31", "include_addresses": True},
        "/api/connector/v1/customers/getAll",
        "Customers",
    ),
    "list_services": (
        {"service_type": "Bookable"},
        "/api/connector/v1/services/getAll",
        "Services",
    ),
    "list_resources": ({"names": ["101"]}, "/api/connector/v1/resources/getAll", "Resources"),
    "list_enterprises": ({}, "/api/connector/v1/enterprises/getAll", "Enterprises"),
    "get_configuration": ({"enterprise_id": GUID}, "/api/connector/v1/configuration/get", None),
}


@pytest.mark.anyio
async def test_all_tools_post_only_documented_read_operations() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/configuration/get"):
            return httpx.Response(200, json={"Enterprise": {"Id": GUID, "Name": "Private Hotel"}})
        items = [{"Id": GUID, "Number": "50", "State": "Confirmed", "Notes": "Private note"}]
        key = request.url.path.removeprefix("/api/connector/v1/").split("/")[0].capitalize()
        return httpx.Response(200, json={key: items, "Cursor": CURSOR})

    definition = build_definition(MewsService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, path, collection) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "POST"
        assert request.url.host == "api.mews.com" and request.url.path == path
        assert request.url.path.removeprefix("/api/connector/v1") in ALLOWED_PATHS
        assert request.headers["content-type"] == "application/json"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["ClientToken"] == CLIENT_TOKEN and body["AccessToken"] == ACCESS_TOKEN
        assert body["Client"] == CLIENT == "OpenMCP 1.0.0"
        text = result.model_dump_json()
        assert CLIENT_TOKEN not in text and ACCESS_TOKEN not in text and PII_KEY not in text
        assert "Private" not in text
        if collection is None:
            assert not any("cursor=" in warning for warning in result.warnings)
        else:
            assert result.data["count"] == 1 and result.data["truncated"] is True
            assert f"cursor={CURSOR}" in result.warnings[-1]
    bodies = [json.loads(request.content) for request in seen]
    assert bodies[0]["CollidingUtc"] == {
        "StartUtc": "2024-01-01T00:00:00Z",
        "EndUtc": "2024-03-31T23:59:59Z",
    }
    assert bodies[0]["States"] == ["Confirmed", "Started"]
    assert bodies[0]["ServiceIds"] == [GUID]
    assert bodies[0]["Limitation"] == {"Count": 50, "Cursor": CURSOR}
    assert "ScheduledStartUtc" not in bodies[0]
    assert bodies[1]["UpdatedUtc"]["StartUtc"] == "2024-01-01T00:00:00Z"
    assert bodies[1]["Extent"] == {"Customers": True, "Addresses": True}
    assert bodies[1]["Limitation"] == {"Count": 25}
    assert bodies[2]["ServiceType"] == "Bookable"
    assert bodies[3]["Extent"] == {"Resources": True, "Inactive": False}
    assert bodies[3]["Names"] == ["101"]
    assert bodies[5]["EnterpriseId"] == GUID and "Limitation" not in bodies[5]


@pytest.mark.anyio
async def test_environment_selects_fixed_origin_and_test_connection() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"Enterprise": {"Id": GUID}})

    service = MewsService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context(credentials={"environment": "demo"})) == {
        "connected": True
    }
    assert seen[0].url.host == "api.mews-demo.com"
    assert seen[0].url.path == "/api/connector/v1/configuration/get"
    assert json.loads(seen[0].content)["Client"] == "OpenMCP 1.0.0"


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"environment": "staging"}},
        {"credentials": {"environment": "https://api.mews.com"}},
        {"credentials": {"environment": ""}},
        {"credentials": {"client_token": "bad token"}},
        {"credentials": {"access_token": "short"}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"provider_credential": None},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"Enterprise": {}})

    service = MewsService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    assert calls == 0


@pytest.mark.anyio
async def test_unlisted_operation_is_refused_without_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = MewsService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await service.post(context(), "/reservations/add", {}, None)
    with pytest.raises(ConnectorError):
        await service.invoke("delete_reservation", build_definition().tools[
            "list_services"
        ].input_model.model_validate({}), context())
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, json={"Message": "Invalid private"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json={"Message": "private token"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, json={"Message": "private rule"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, text="private"), ErrorCode.NOT_FOUND),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[{"Id": GUID}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"Message": "private"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"Services": "private"}), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = MewsService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["list_services"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_reservations", {}),
        ("list_reservations", {"start": "2024-01-01"}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-05-01"}),
        ("list_reservations", {"start": "2024-02-01", "end": "2024-01-01"}),
        ("list_reservations", {"start": "2024-13-01", "end": "2024-12-31"}),
        ("list_reservations", {"start": "2024-01-01T00:00:00", "end": "2024-01-02"}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "count": 0}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "count": 101}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "cursor": "abc"}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "states": []}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "states": ["Paid"]}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "window": "actual"}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "service_ids": ["x"]}),
        ("list_reservations", {"start": "2024-01-01", "end": "2024-01-02", "url": "https://e"}),
        ("list_customers", {}),
        ("list_customers", {"start": "2024-01-01"}),
        ("list_customers", {"customer_ids": []}),
        ("list_customers", {"customer_ids": [GUID] * 51}),
        ("list_customers", {"customer_ids": [GUID], "include_addresses": "yes"}),
        ("list_services", {"service_type": "Spa"}),
        ("list_resources", {"names": [""]}),
        ("list_resources", {"names": "101"}),
        ("list_enterprises", {"count": "10"}),
        ("get_configuration", {"enterprise_id": GUID + "x"}),
        ("get_configuration", []),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
