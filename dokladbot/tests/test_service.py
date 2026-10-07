from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_dokladbot.app import build_definition
from connector_dokladbot.service import SLUG, DokladbotService

API_KEY = "dkb_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"


def context(**updates: Any) -> InvocationContext:
    values = {"api_key": API_KEY, "pii_key": PII_KEY}
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref=f"{SLUG}/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_all_tools_use_fixed_paths_get_and_bearer() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [{"id": "abc", "merchant": "Jan Novak"}]})

    definition = build_definition(DokladbotService(transport=httpx.MockTransport(upstream)))
    cases: dict[str, tuple[dict[str, Any], str, dict[str, str]]] = {
        "list_documents": ({}, "/api/receipts", {"page": "1", "limit": "20", "status": "ALL"}),
        "get_document": ({"document_id": "demo-rev-1"}, "/api/receipts/demo-rev-1", {}),
        "get_vat_summary": (
            {"year": 2026, "quarter": 3},
            "/api/dph",
            {"year": "2026", "quarter": "3"},
        ),
        "list_due_invoices": ({}, "/api/due-dates", {"status": "overdue", "days": "30"}),
        "get_overview": ({}, "/api/dashboard", {}),
        "get_credit_balance": ({}, "/api/credits/balance", {}),
        "list_companies": ({}, "/api/companies", {}),
        "list_suppliers": ({"limit": 5}, "/api/suppliers", {"limit": "5"}),
    }
    assert set(cases) == set(definition.tools)
    for name, (arguments, path, params) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only
        assert request.method == "GET"
        assert request.url.host == "dokladbot.cz"
        assert request.url.path == path
        assert dict(request.url.params) == params
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert request.headers["user-agent"].startswith("OpenMCP/")
        assert "Jan Novak" not in result.model_dump_json()
        assert API_KEY not in result.model_dump_json()


@pytest.mark.anyio
async def test_test_connection_reads_credit_balance() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/credits/balance"
        return httpx.Response(200, json={"total": 10})

    service = DokladbotService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "api_key",
    ["", "short", "dkb_test_" + "A" * 32, "dkb_live_" + "A" * 31, "dkb_live_" + "A" * 31 + "/"],
)
async def test_invalid_api_key_is_rejected_before_network(api_key: str) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = DokladbotService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(credentials={"api_key": api_key}))
    assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_provider_errors_fail_closed_without_leaking(status: int) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "secret detail " + API_KEY})

    service = DokladbotService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert API_KEY not in str(caught.value)
    assert "secret detail" not in str(caught.value)


@pytest.mark.anyio
async def test_error_payload_and_non_object_are_rejected() -> None:
    for body in ({"error": "Neprihlasen"}, ["list"]):

        def upstream(request: httpx.Request, body: Any = body) -> httpx.Response:
            return httpx.Response(200, json=body)

        service = DokladbotService(transport=httpx.MockTransport(upstream))
        with pytest.raises(ConnectorError) as caught:
            await service.test_connection(context())
        assert caught.value.code is ErrorCode.UPSTREAM_ERROR


@pytest.mark.anyio
async def test_redirect_is_not_followed() -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(308, headers={"location": "https://evil.invalid/api/receipts"})

    service = DokladbotService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await service.test_connection(context())
    assert all("evil.invalid" not in url for url in seen)


def test_foreign_installation_is_forbidden() -> None:
    with pytest.raises(ConnectorError) as caught:
        DokladbotService._credentials(context(secret_ref=f"{SLUG}/other/inst-1"))
    assert caught.value.code is ErrorCode.FORBIDDEN


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("list_documents", {"page": 0}),
        ("list_documents", {"limit": 51}),
        ("list_documents", {"status": "DELETED"}),
        ("list_documents", {"url": "https://evil.invalid"}),
        ("get_document", {"document_id": "../admin"}),
        ("get_document", {"document_id": "a/b"}),
        ("get_document", {}),
        ("get_vat_summary", {"year": 2026, "quarter": 2, "month": 5}),
        ("get_vat_summary", {"year": 1999}),
        ("get_vat_summary", {"year": "2026"}),
        ("list_due_invoices", {"status": "all"}),
        ("list_due_invoices", {"days": 0}),
        ("list_suppliers", {"limit": 101}),
        ("get_overview", {"companyId": "x"}),
        ("get_credit_balance", []),
    ],
)
def test_arguments_are_closed_and_bounded(name: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[name].input_model.model_validate(arguments)
