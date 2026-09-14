from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_fakturoid.app import build_definition
from connector_fakturoid.service import FakturoidService


def context(**updates: Any) -> InvocationContext:
    values = {
        "account_slug": "synthetic-account",
        "access_token": "synthetic-access-token-0123456789",
        "pii_key": "synthetic-pii-key-0123456789abcdef",
    }
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="fakturoid/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_all_tools_use_fixed_account_paths_and_bearer() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"id": 7, "client_name": "Private Person"}])

    definition = build_definition(FakturoidService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_invoices": ({}, "/api/v3/accounts/synthetic-account/invoices.json"),
        "get_invoice": ({"invoice_id": 7}, "/api/v3/accounts/synthetic-account/invoices/7.json"),
        "list_subjects": ({}, "/api/v3/accounts/synthetic-account/subjects.json"),
        "get_subject": ({"subject_id": 7}, "/api/v3/accounts/synthetic-account/subjects/7.json"),
        "list_expenses": ({}, "/api/v3/accounts/synthetic-account/expenses.json"),
        "get_expense": ({"expense_id": 7}, "/api/v3/accounts/synthetic-account/expenses/7.json"),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].url.path == path
        assert seen[-1].headers["authorization"].startswith("Bearer ")
        assert seen[-1].headers["user-agent"].startswith("OpenMCP/")
        assert "Private Person" not in result.model_dump_json()
    assert seen[0].url.params["page"] == "1"


@pytest.mark.anyio
async def test_invalid_account_and_oversized_page_fail_closed() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[{"id": value} for value in range(41)])

    service = FakturoidService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await service.test_connection(context(credentials={"account_slug": "../evil"}))
    assert calls == 0
    with pytest.raises(ConnectorError):
        await service.test_connection(context())


@pytest.mark.parametrize("arguments", [{"page": 0}, {"page": True}, {"url": "https://evil"}, []])
def test_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools["list_invoices"].input_model.model_validate(arguments)
