from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_reservio.app import build_definition
from connector_reservio.service import BUSINESS_PATHS, SLUG, ReservioService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
ACCESS_TOKEN = "synthetic-reservio-access-token-0123456789"
BUSINESS = "b04965e6-a9bb-591f-8f8a-1adcb2c8dc39"
SERVICE = "4b166dbe-d99d-5091-abdd-95b83330ed3a"
RESOURCE = "98123fde-012f-5ff3-8b50-881449dac91a"
BASE = f"/v2/businesses/{BUSINESS}"


def context(**changes: Any) -> InvocationContext:
    values = {"access_token": ACCESS_TOKEN, "business_id": BUSINESS, "pii_key": PII_KEY}
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


CASES: dict[str, tuple[dict[str, Any], str, dict[str, str]]] = {
    "get_business": ({}, BASE, {}),
    "list_services": ({}, f"{BASE}/services", {}),
    "get_service": ({"service_id": SERVICE}, f"{BASE}/services/{SERVICE}", {}),
    "list_resources": ({}, f"{BASE}/resources", {}),
    "list_opening_hours": ({}, f"{BASE}/opening-hours", {}),
    "booking_slots": (
        {
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "service_id": SERVICE,
            "resource_id": RESOURCE,
        },
        f"{BASE}/availability/booking-slots",
        {
            "filter[from]": "2024-01-01",
            "filter[to]": "2024-01-31",
            "filter[serviceId]": SERVICE,
            "filter[resourceId]": RESOURCE,
        },
    ),
    "list_events": ({}, f"{BASE}/events", {"sort": "-createdAt"}),
    "list_bookings": ({"sort": "createdAt"}, f"{BASE}/bookings", {"sort": "createdAt"}),
}


def item(kind: str) -> dict[str, Any]:
    return {
        "type": kind,
        "id": SERVICE,
        "attributes": {"name": "Private name", "email": "private@example.test", "cost": 200},
    }


@pytest.mark.anyio
async def test_all_tools_get_documented_paths_with_bearer_and_jsonapi_accept() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path in (BASE, f"{BASE}/services/{SERVICE}"):
            return httpx.Response(200, json={"data": item("business")})
        return httpx.Response(
            200,
            json={
                "meta": {"total": 7},
                "links": {"self": "https://api.reservio.com/private", "next": None},
                "data": [item("service")],
            },
        )

    definition = build_definition(ReservioService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, path, params) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.host == "api.reservio.com" and request.url.path == path
        assert request.url.path.startswith(BASE)
        assert dict(request.url.params) == params
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert request.headers["accept"] == "application/vnd.api+json"
        assert request.headers["user-agent"].startswith("OpenMCP/")
        text = result.model_dump_json()
        assert ACCESS_TOKEN not in text and PII_KEY not in text and "rivate" not in text
        if isinstance(result.data["data"], list):
            assert result.data["count"] == 1 and result.data["total"] == 7
            assert result.data["truncated"] is False
        else:
            assert "count" not in result.data
    assert len(seen) == len(CASES) == len(BUSINESS_PATHS)


@pytest.mark.anyio
async def test_next_link_marks_truncated_and_test_connection_uses_users_me() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v2/users/me":
            return httpx.Response(200, json={"data": {"type": "user", "id": SERVICE}})
        return httpx.Response(
            200,
            json={
                "links": {"next": "https://api.reservio.com/v2/private?page=2"},
                "data": [item("event"), item("event")],
            },
        )

    service = ReservioService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/v2/users/me"
    spec = build_definition(service).tools["list_events"]
    result = await spec.handler(spec.input_model.model_validate({}), context())
    assert result.data["count"] == 2 and result.data["truncated"] is True
    assert "total" not in result.data and "private" not in result.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"access_token": "short"}},
        {"credentials": {"access_token": "bad token with spaces 0123456789"}},
        {"credentials": {"access_token": ""}},
        {"credentials": {"business_id": "not-a-guid"}},
        {"credentials": {"business_id": BUSINESS + "/x"}},
        {"credentials": {"business_id": ""}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_version": None},
        {"provider_credential": None},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": []})

    service = ReservioService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_services"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    with pytest.raises(ConnectorError):
        await service.test_connection(context(**changes))
    assert calls == 0


@pytest.mark.anyio
async def test_unknown_tool_is_refused_without_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": []})

    service = ReservioService(transport=httpx.MockTransport(upstream))
    model = build_definition().tools["list_services"].input_model
    for unknown in ("create_booking", "cancel_booking", "delete_event"):
        with pytest.raises(ConnectorError) as caught:
            await service.invoke(unknown, model.model_validate({}), context())
        assert caught.value.code is ErrorCode.NOT_FOUND
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, json={"errors": [{"detail": "private"}]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json={"errors": [{"detail": "p"}]}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, text="private rule"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, text="private"), ErrorCode.NOT_FOUND),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[{"id": 1}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"errors": [{"detail": "private"}]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"data": "private"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"meta": {"total": 1}}), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = ReservioService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["list_services"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


SLOTS = {"date_from": "2024-01-01", "date_to": "2024-01-31", "service_id": SERVICE}


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("get_business", {"business_id": BUSINESS}),
        ("get_business", {"url": "https://evil"}),
        ("get_business", []),
        ("get_service", {}),
        ("get_service", {"service_id": "abc"}),
        ("get_service", {"service_id": SERVICE + "x"}),
        ("get_service", {"service_id": 7}),
        ("booking_slots", {}),
        ("booking_slots", {"date_from": "2024-01-01", "date_to": "2024-01-31"}),
        ("booking_slots", SLOTS | {"date_to": "2023-12-31"}),
        ("booking_slots", SLOTS | {"date_to": "2024-04-03"}),
        ("booking_slots", SLOTS | {"date_from": "2024-13-01"}),
        ("booking_slots", SLOTS | {"date_from": "2024-1-1"}),
        ("booking_slots", SLOTS | {"date_from": "2024-01-01T00:00:00"}),
        ("booking_slots", SLOTS | {"resource_id": "x"}),
        ("booking_slots", SLOTS | {"page": 2}),
        ("list_events", {"sort": "-start"}),
        ("list_events", {"sort": ""}),
        ("list_bookings", {"filter": "x"}),
        ("list_bookings", {"sort": "createdAt\r\n"}),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
