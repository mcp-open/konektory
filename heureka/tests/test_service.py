from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_heureka import schemas as s
from connector_heureka.app import build_definition
from connector_heureka.service import READ_OPERATIONS, SLUG, HeurekaService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
API_KEY = "e9a09238d23fb504c663d06553ca4838"
PRIVATE = "Pobočka na náměstí"
INVALID = ErrorCode.CREDENTIAL_INVALID
UPSTREAM = ErrorCode.UPSTREAM_ERROR


def context(**changes: Any) -> InvocationContext:
    values = {"api_key": API_KEY, "country": "cz", "pii_key": PII_KEY}
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


CASES: dict[str, tuple[dict[str, Any], str]] = {
    "get_order_status": ({"order_id": 1234}, f"/api/cart/{API_KEY}/1/order/status"),
    "list_stores": ({}, f"/api/cart/{API_KEY}/1/stores"),
    "get_shop_status": ({}, f"/api/cart/{API_KEY}/1/shop/status"),
}


@pytest.mark.anyio
async def test_all_tools_use_documented_get_paths_with_key_segment() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.host == "ssl.heureka.cz"
        assert "authorization" not in request.headers
        assert request.headers["user-agent"].startswith("OpenMCP/")
        assert not request.content
        if request.url.path.endswith("/stores"):
            return httpx.Response(
                200, json=[{"id": 390, "type": 1, "name": PRIVATE, "city": "Brno"}]
            )
        return httpx.Response(
            200,
            json={"order_id": 1234, "status": 1, "internal_id": "8100000630", "heureka_id": 97},
        )

    definition = build_definition(HeurekaService(transport=httpx.MockTransport(upstream)))
    assert set(definition.tools) == set(CASES) == set(READ_OPERATIONS)
    for name, (arguments, path) in CASES.items():
        spec = definition.tools[name]
        assert spec.read_only
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert seen[-1].url.path == path
        text = result.model_dump_json()
        assert PRIVATE not in text and "Brno" not in text and "8100000630" not in text
        assert API_KEY not in text and PII_KEY not in text
        operation = path.removeprefix(f"/api/cart/{API_KEY}/1")
        expected = f"https://ssl.heureka.cz/api/cart/{{api_key}}/1{operation}"
        assert result.provenance.source_url == expected
    assert len(seen) == len(CASES)
    assert dict(seen[0].url.params) == {"order_id": "1234"}
    assert not seen[1].url.query and not seen[2].url.query


@pytest.mark.anyio
async def test_country_selects_fixed_origin_and_test_connection() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": True, "error": []})

    service = HeurekaService(transport=httpx.MockTransport(upstream))
    slovak = context(credentials={"country": "sk"})
    assert await service.test_connection(slovak) == {"connected": True}
    assert seen[-1].url.host == "ssl.heureka.sk"
    assert seen[-1].url.path == f"/api/cart/{API_KEY}/1/shop/status"
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[-1].url.host == "ssl.heureka.cz"


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"country": "hu"}},
        {"credentials": {"country": "CZ"}},
        {"credentials": {"country": "https://ssl.heureka.cz"}},
        {"credentials": {"country": ""}},
        {"credentials": {"api_key": "short"}},
        {"credentials": {"api_key": "bad key with spaces 0123456789"}},
        {"credentials": {"api_key": "../../1/order/status?x=0123456789"}},
        {"credentials": {"api_key": ""}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_ref": "other/workspace-1/install-1"},
        {"secret_version": None},
        {"provider_credential": None},
        {"provider_credential": {}},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": True})

    service = HeurekaService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["get_order_status"]
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (INVALID, ErrorCode.FORBIDDEN)
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"order_id": 1}), context(**changes))
    assert caught.value.code in (INVALID, ErrorCode.FORBIDDEN)
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, json={"message": "private"}), UPSTREAM),
        (httpx.Response(401, text="private"), INVALID),
        (httpx.Response(403, json={"message": "private key"}), INVALID),
        (httpx.Response(404, text="private"), ErrorCode.NOT_FOUND),
        (httpx.Response(422, json={"message": "Validation error(s)!"}), UPSTREAM),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"private not json"), UPSTREAM),
        (httpx.Response(200, content=b"<html>private</html>"), UPSTREAM),
        (httpx.Response(200, json="private"), UPSTREAM),
        (httpx.Response(200, json=42), UPSTREAM),
        (httpx.Response(200, json=None), UPSTREAM),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_map_to_safe_codes(
    response: httpx.Response, code: ErrorCode
) -> None:
    service = HeurekaService(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(ConnectorError) as caught:
        await service.get(context(), "/order/status", {"order_id": 1})
    assert caught.value.code is code
    assert "private" not in str(caught.value) and "Validation" not in str(caught.value)
    assert API_KEY not in str(caught.value)


@pytest.mark.anyio
async def test_unlisted_operations_and_write_tools_are_refused_without_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": True})

    service = HeurekaService(transport=httpx.MockTransport(upstream))
    for operation in ("/order/cancel", "/payment/status", "/order/note", "/payout-report"):
        with pytest.raises(ConnectorError) as caught:
            await service.get(context(), operation)
        assert caught.value.code is ErrorCode.INTERNAL
    arguments = build_definition().tools["list_stores"].input_model.model_validate({})
    for name in ("set_order_status", "cancel_order", "send_invoice", "confirm_order"):
        with pytest.raises(ConnectorError) as unknown:
            await service.invoke(name, arguments, context())
        assert unknown.value.code is ErrorCode.NOT_FOUND
    assert calls == 0


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.OrderID, {}),
        (s.OrderID, {"order_id": 0}),
        (s.OrderID, {"order_id": -5}),
        (s.OrderID, {"order_id": "1234"}),
        (s.OrderID, {"order_id": True}),
        (s.OrderID, {"order_id": 1.0}),
        (s.OrderID, {"order_id": 2**53}),
        (s.OrderID, {"order_id": 1, "status": 10}),
        (s.OrderID, {"order_id": 1, "url": "https://outside.invalid"}),
        (s.Empty, {"limit": 1}),
        (s.Empty, {"name": "evil\r\nHost: other"}),
        (s.Empty, []),
        (s.Empty, "text"),
        (s.Empty, None),
    ],
)
def test_arguments_are_closed_and_bounded(model: type[s.Input], arguments: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
    assert s.OrderID.model_validate({"order_id": 2**53 - 1}).order_id == 2**53 - 1
    assert s.Empty.model_validate({}).model_dump() == {}
