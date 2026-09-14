from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_mapy.app import build_definition
from connector_mapy.service import MapyService, coordinate

KEY = "synthetic-private-api-key-0123456789"


def context(**updates: Any) -> InvocationContext:
    values = {"api_key": KEY, "pii_key": "synthetic-pii-key-0123456789abcdef"}
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="mapy/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_all_tools_use_documented_paths_and_header_key() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        items = [{"name": "Private Street 1", "zip": "11000"}]
        return httpx.Response(200, json={"items": items})

    definition = build_definition(MapyService(transport=httpx.MockTransport(upstream)))
    cases = {
        "geocode": ({"query": "Praha"}, "/v1/geocode"),
        "suggest": (
            {"query": "Pra", "limit": 3, "entity_type": "poi", "locality": "cz"},
            "/v1/suggest",
        ),
        "reverse_geocode": ({"lon": 14.4, "lat": 50}, "/v1/rgeocode"),
        "route": (
            {
                "start": {"lon": 14.4, "lat": 50.07},
                "end": {"lon": 16.6, "lat": 49.2},
                "route_type": "foot_hiking",
                "avoid_toll": True,
                "waypoints": [{"lon": 15.5, "lat": 49.5}],
            },
            "/v1/routing/route",
        ),
        "elevation": (
            {"positions": [{"lon": 14.4, "lat": 50.07}, {"lon": -0.1, "lat": 51.5}]},
            "/v1/elevation",
        ),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].method == "GET" and seen[-1].url.path == path
        assert seen[-1].headers["x-mapy-api-key"] == KEY
        assert "apikey" not in seen[-1].url.params and "authorization" not in seen[-1].headers
        serialized = result.model_dump_json()
        assert "Private Street" not in serialized and KEY not in serialized
    assert seen[0].url.params["limit"] == "5" and seen[0].url.params["lang"] == "cs"
    assert seen[1].url.params["type"] == "poi" and seen[1].url.params["locality"] == "cz"
    assert seen[2].url.params["lon"] == "14.4" and seen[2].url.params["lat"] == "50"
    route = seen[3].url.params
    assert route["start"] == "14.4,50.07" and route["end"] == "16.6,49.2"
    assert route["routeType"] == "foot_hiking" and route["format"] == "polyline"
    assert route["avoidToll"] == "true" and route["avoidHighways"] == "false"
    assert route["waypoints"] == "15.5,49.5"
    assert seen[4].url.params.get_list("positions") == ["14.4,50.07", "-0.1,51.5"]


def test_coordinate_formatting_is_plain_decimal() -> None:
    assert coordinate({"lon": 0, "lat": -0.0}) == "0,0"
    assert coordinate({"lon": 1e-7, "lat": 180}) == "0.0000001,180"
    with pytest.raises(ConnectorError):
        coordinate({"lon": True, "lat": 1})


@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"items": []})

    service = MapyService(transport=httpx.MockTransport(upstream))
    for bad in ({"api_key": "short"}, {"api_key": "bad key\n"}, {"api_key": ""}):
        with pytest.raises(ConnectorError) as caught:
            await service.test_connection(context(credentials=bad))
        assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(secret_ref="mapy/other/inst-1"))
    assert caught.value.code is ErrorCode.FORBIDDEN
    assert calls == 0
    assert await service.test_connection(context()) == {"connected": True}
    assert calls == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"error": "private detail"}),
        httpx.Response(200, json={"detail": [{"msg": "private detail"}]}),
        httpx.Response(200, json=["private detail"]),
        httpx.Response(200, content=b"<html>private detail</html>"),
        httpx.Response(401, json={"detail": "private detail"}),
        httpx.Response(429, json={"detail": "private detail"}),
        httpx.Response(500, text="private detail"),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response) -> None:
    service = MapyService(transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert "private detail" not in str(caught.value)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("geocode", {"query": ""}),
        ("geocode", {"query": "x" * 151}),
        ("geocode", {"query": "Praha", "limit": 0}),
        ("geocode", {"query": "Praha", "limit": 16}),
        ("geocode", {"query": "Praha", "lang": "xx"}),
        ("geocode", {"query": "Praha", "apikey": "other"}),
        ("suggest", {"query": "Praha", "entity_type": "street"}),
        ("reverse_geocode", {"lon": 181, "lat": 50}),
        ("reverse_geocode", {"lon": 14, "lat": -91}),
        ("reverse_geocode", {"lon": "14.4", "lat": 50}),
        ("reverse_geocode", {"lon": True, "lat": 50}),
        ("reverse_geocode", {"lon": float("nan"), "lat": 50}),
        ("reverse_geocode", {"lon": float("inf"), "lat": 50}),
        ("route", {"start": {"lon": 14, "lat": 50}, "end": {"lon": 200, "lat": 50}}),
        (
            "route",
            {"start": {"lon": 14, "lat": 50}, "end": {"lon": 15, "lat": 50}, "route_type": "plane"},
        ),
        ("route", {"start": {"lon": 14, "lat": 50, "z": 1}, "end": {"lon": 15, "lat": 50}}),
        ("route", {"start": "14,50", "end": "15,50"}),
        (
            "route",
            {
                "start": {"lon": 14, "lat": 50},
                "end": {"lon": 15, "lat": 50},
                "waypoints": [{"lon": 14, "lat": 50}] * 16,
            },
        ),
        ("elevation", {"positions": []}),
        ("elevation", {"positions": [{"lon": 14, "lat": 50}] * 65}),
        ("elevation", {"positions": [{"lon": 14, "lat": 95}]}),
        ("elevation", []),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
