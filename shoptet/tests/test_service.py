from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_shoptet.app import build_definition
from connector_shoptet.service import ShoptetService

TOKEN = "synthetic-private-access-token-0123456789abcdef"
GUID = "1b02cb8e-d7b5-11e0-9a5c-feab5ed617ed"


def context(**updates: Any) -> InvocationContext:
    values = {"access_token": TOKEN, "pii_key": "synthetic-pii-key-0123456789abcdef"}
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="shoptet/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_all_tools_use_documented_paths_and_token_header() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        orders = [{"code": "2017000092", "fullName": "Private Person"}]
        return httpx.Response(200, json={"data": {"orders": orders}, "errors": None})

    definition = build_definition(ShoptetService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_orders": (
            {"page": 2, "status_id": 3, "creation_time_from": "2024-01-01T00:00:00+0100"},
            "/api/orders",
        ),
        "get_order": ({"code": "2017000092"}, "/api/orders/2017000092"),
        "list_products": (
            {"product_type": "service", "category_guid": GUID, "include_images": True},
            "/api/products",
        ),
        "get_product": ({"guid": GUID}, f"/api/products/{GUID}"),
        "get_product_by_code": ({"code": "ABC-123"}, "/api/products/code/ABC-123"),
        "list_customers": ({"items_per_page": 100}, "/api/customers"),
        "get_customer": ({"guid": GUID}, f"/api/customers/{GUID}"),
        "list_stocks": ({}, "/api/stocks"),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].method == "GET" and seen[-1].url.path == path
        assert seen[-1].headers["shoptet-access-token"] == TOKEN
        assert "authorization" not in seen[-1].headers and "token" not in str(seen[-1].url)
        assert seen[-1].headers["user-agent"].startswith("OpenMCP/")
        serialized = result.model_dump_json()
        assert "Private Person" not in serialized and TOKEN not in serialized
    orders = seen[0].url.params
    assert orders["page"] == "2" and orders["itemsPerPage"] == "50" and orders["statusId"] == "3"
    assert orders["creationTimeFrom"] == "2024-01-01T00:00:00+0100"
    assert "changeTimeFrom" not in orders
    assert not seen[1].url.params
    products = seen[2].url.params
    assert products["page"] == "1" and products["itemsPerPage"] == "20"
    assert products["type"] == "service" and products["categoryGuid"] == GUID
    assert products["include"] == "images"
    assert seen[5].url.params["itemsPerPage"] == "100"
    assert not seen[7].url.params


@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": {"contactInformation": {}}, "errors": []})

    service = ShoptetService(transport=httpx.MockTransport(upstream))
    bad_tokens = ("short", "bad tok\n" * 4, "")
    for bad in ({"access_token": token} for token in bad_tokens):
        with pytest.raises(ConnectorError) as caught:
            await service.test_connection(context(credentials=bad))
        assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(secret_ref="shoptet/other/inst-1"))
    assert caught.value.code is ErrorCode.FORBIDDEN
    assert calls == 0
    assert await service.test_connection(context()) == {"connected": True}
    assert calls == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"data": None, "errors": [{"message": "private detail"}]}),
        httpx.Response(200, json={"data": {"x": 1}, "errors": [{"message": "private detail"}]}),
        httpx.Response(200, json={"errors": [], "message": "private detail"}),
        httpx.Response(200, json=["private detail"]),
        httpx.Response(200, content=b"<html>private detail</html>"),
        httpx.Response(401, json={"errors": [{"message": "private detail"}]}),
        httpx.Response(404, json={"errors": [{"message": "private detail"}]}),
        httpx.Response(429, json={"errors": [{"message": "private detail"}]}),
        httpx.Response(500, text="private detail"),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response) -> None:
    service = ShoptetService(transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert "private detail" not in str(caught.value)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_orders", {"page": 0}),
        ("list_orders", {"page": 10_001}),
        ("list_orders", {"items_per_page": 51}),
        ("list_orders", {"items_per_page": "10"}),
        ("list_orders", {"status_id": 0}),
        ("list_orders", {"creation_time_from": "2024-01-01"}),
        ("list_orders", {"creation_time_from": "2024-01-01T00:00:00"}),
        ("list_orders", {"email": "someone@example.test"}),
        ("list_orders", {"url": "https://evil"}),
        ("get_order", {"code": ""}),
        ("get_order", {"code": "../orders"}),
        ("get_order", {"code": "a/b"}),
        ("get_order", {"code": 12}),
        ("list_products", {"items_per_page": 101}),
        ("list_products", {"product_type": "voucher"}),
        ("list_products", {"category_guid": "not-a-guid"}),
        ("list_products", {"include_images": "yes"}),
        ("get_product", {"guid": "1b02cb8e-d7b5-11e0-9a5c"}),
        ("get_product_by_code", {"code": "ab c"}),
        ("list_customers", {"items_per_page": 1000}),
        ("get_customer", {"guid": GUID, "email": "x"}),
        ("list_stocks", {"stock_id": 1}),
        ("list_stocks", []),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
