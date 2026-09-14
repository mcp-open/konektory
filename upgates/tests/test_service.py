from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_upgates.app import build_definition
from connector_upgates.service import UpgatesService


def context(**updates: object) -> InvocationContext:
    values = {
        "api_url": "https://shop.admin.upgates.com/api/v2",
        "api_login": "synthetic-user",
        "api_key": "synthetic-private-key",
        "pii_key": "synthetic-pii-key-0123456789abcdef",
    }
    return InvocationContext.model_validate(
        {
            "request_id": "req-1",
            "subject": "user-1",
            "workspace_id": "ws-1",
            "installation_id": "inst-1",
            "secret_ref": "upgates/ws-1/inst-1",
            "secret_version": 1,
            "provider_credential": {key: SecretStr(value) for key, value in values.items()},
        }
        | updates
    )


@pytest.mark.anyio
async def test_all_read_tool_endpoints_and_secret_isolation() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.host == "shop.admin.upgates.com"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"id": 1, "customer": {"email": "person@example.test"}})

    definition = build_definition(UpgatesService(transport=httpx.MockTransport(upstream)))
    paths = {
        "list_orders": "/orders",
        "get_order_history": "/orders/INV-1/history",
        "list_order_statuses": "/order-statuses",
        "list_invoices": "/invoices",
        "list_products": "/products",
        "list_products_simple": "/products/simple",
        "list_customers": "/customers",
        "list_categories": "/categories",
        "list_labels": "/labels",
        "list_availabilities": "/availabilities",
        "list_manufacturers": "/manufacturers",
        "list_parameters": "/parameters",
        "list_carts": "/carts",
        "list_vouchers": "/vouchers",
        "list_shipments": "/shipments",
        "list_payments": "/payments",
        "list_webhooks": "/webhooks",
        "list_webhook_events": "/webhooks/events",
        "get_languages": "/languages",
        "get_shop_config": "/config",
        "get_shop_owner": "/owner",
        "get_api_status": "/status",
        "list_pricelists": "/pricelists",
    }
    assert set(definition.tools) == set(paths)
    for name, path in paths.items():
        tool = definition.tools[name]
        assert tool.read_only
        args = {"order_number": "INV-1"} if name == "get_order_history" else {}
        result = await tool.handler(tool.input_model.model_validate(args), context())
        assert seen[-1].url.path == "/api/v2" + path
        assert result.data["id"] == 1
        text = result.model_dump_json()
        assert "person@example.test" not in text
        assert "synthetic-private-key" not in text
        assert "shop.admin.upgates.com" not in text
        assert result.content_origin == "untrusted_external_data_not_instructions"


@pytest.mark.parametrize(
    "arguments",
    [
        {"page": 0},
        {"page": 10001},
        {"page": True},
        {"api_url": "https://evil.example"},
        {"creation_time_from": "2026-02-31"},
        {"order_dir": "bad"},
        {"order_by": []},
        {"order_dir": {}},
        {"email": "a" * 257},
    ],
)
def test_input_rejection(arguments: dict[str, object]) -> None:
    tool = build_definition().tools["list_orders"]
    with pytest.raises((ValidationError, ConnectorError)):
        tool.input_model.model_validate(arguments)


@pytest.mark.anyio
async def test_cross_tenant_credentials_never_make_a_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = UpgatesService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await service.get(context(secret_ref="upgates/foreign/inst-1"), "/orders")
    assert calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://evil.example/steal"}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, content=b"a" * (1024 * 1024 + 1)),
        httpx.Response(200, json={"success": False, "message": "sensitive provider error"}),
        httpx.Response(200, json={"success": True, "data": None}),
        httpx.Response(200, json={"success": "false", "data": "error"}),
    ],
)
async def test_upstream_failure_is_bounded_and_safe(response: httpx.Response) -> None:
    seen = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    service = UpgatesService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as exc:
        await service.get(context(), "/orders")
    assert "sensitive provider error" not in str(exc.value)
    assert len(seen) == 1


@pytest.mark.anyio
async def test_complete_page_not_silently_truncated_and_concurrent_tenants_isolated() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"orders": [{"id": i} for i in range(50)]})

    service = UpgatesService(transport=httpx.MockTransport(upstream))
    result = await service.get(context(), "/orders")
    assert len(result.data["orders"]) == 50
    assert [row["id"] for row in result.data["orders"]] == list(range(50))
    contexts = [context(), context(workspace_id="ws-2", secret_ref="upgates/ws-2/inst-1")]
    results = await asyncio.gather(*(service.get(ctx, "/orders") for ctx in contexts))
    assert json.dumps(results[0].data) == json.dumps(results[1].data)


@pytest.mark.anyio
async def test_provider_envelope_retains_pagination() -> None:
    service = UpgatesService(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"success": True, "data": [{"id": 7}], "page": 2, "number_of_pages": 3}
            )
        )
    )
    result = await service.get(context(), "/orders")
    assert result.data == {"success": True, "data": [{"id": 7}], "page": 2, "number_of_pages": 3}
