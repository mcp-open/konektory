from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_superfaktura.app import build_definition
from connector_superfaktura.service import SuperfakturaService


def context(**updates: Any) -> InvocationContext:
    values = {
        "region": "sandbox-sk",
        "email": "synthetic+api@example.test",
        "api_key": "synthetic-private-key",
        "pii_key": "synthetic-pii-key-0123456789abcdef",
    }
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="superfaktura/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_all_tools_are_read_only_and_use_fixed_paths() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [{"id": 7, "email": "private@example.test"}]})

    definition = build_definition(SuperfakturaService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_invoices": ({}, "/invoices/index.json/listinfo:1/per_page:50/page:1"),
        "get_invoice": ({"invoice_id": 7}, "/invoices/view/7.json"),
        "list_clients": ({}, "/clients/index.json/listinfo:1/per_page:50/page:1"),
        "get_client": ({"client_id": 7}, "/clients/view/7.json"),
        "list_expenses": ({}, "/expenses/index.json/listinfo:1/per_page:50/page:1"),
        "get_expense": ({"expense_id": 7}, "/expenses/view/7.json"),
    }
    assert set(definition.tools) == set(cases)
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].url.path == path
        assert seen[-1].url.host == "sandbox.superfaktura.sk"
        assert "email=synthetic%2Bapi%40example.test" in seen[-1].headers["authorization"]
        assert "private@example.test" not in result.model_dump_json()


@pytest.mark.anyio
async def test_bad_credentials_and_provider_errors_fail_closed() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"error": 1, "error_message": "secret"})

    service = SuperfakturaService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await service.test_connection(context(credentials={"region": "evil"}))
    assert calls == 0
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("arguments", [{"page": 0}, {"per_page": 101}, {"url": "https://evil"}, []])
def test_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools["list_invoices"].input_model.model_validate(arguments)
