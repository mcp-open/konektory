from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_kros import schemas as s
from connector_kros.app import build_definition
from connector_kros.service import ORIGIN, SLUG, KrosService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
API_TOKEN = "synthetic-kros-api-token-0123456789abcdef"
PRIVATE = "Private partner s.r.o."
EXPENSE = "3F2504E0-4F89-11D3-9A0C-0305E82C3301"


def context(**changes: Any) -> InvocationContext:
    values = {"api_token": API_TOKEN, "pii_key": PII_KEY}
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


@pytest.mark.anyio
async def test_every_tool_gets_documented_path_with_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        data = [{"id": 7, "documentNumber": "FA2026001", "partner": {"name": PRIVATE}}]
        return httpx.Response(200, json={"data": data})

    definition = build_definition(KrosService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_invoices": (
            {
                "issue_date_from": "2026-01-01",
                "issue_date_to": "2026-03-31",
                "payment_status": "not_paid",
                "due_date_status": "over_due",
                "extended_fields": ["Items", "Payments", "Items"],
                "top": 10,
                "skip": 20,
            },
            "/api/invoices",
        ),
        "get_invoice": ({"invoice_id": 7}, "/api/invoices/7"),
        "list_proforma_invoices": ({"order_number": "OBJ-1"}, "/api/proforma-invoices"),
        "list_expenses": (
            {"due_date_from": "2026-01-01", "last_modified_from": "2026-01-01T00:00:00"},
            "/api/expenses",
        ),
        "get_expense": ({"expense_id": EXPENSE}, f"/api/expenses/{EXPENSE.lower()}"),
        "list_catalog_items": (
            {
                "name": "šroub",
                "only_marked_for_eshop": True,
                "last_modified_from": "2026-01-01T00:00:00",
            },
            "/api/catalog-items",
        ),
        "list_payments": ({"account_id": 3, "payment_date_from": "2026-02-01"}, "/api/payments"),
        "list_bank_accounts": ({}, "/api/payments/accounts"),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].method == "GET"
        assert seen[-1].url.host == "api-economy.kros.sk" and seen[-1].url.path == path
        assert seen[-1].headers["authorization"] == f"Bearer {API_TOKEN}"
        assert seen[-1].headers["user-agent"].startswith("OpenMCP/")
        dumped = result.model_dump_json()
        assert PRIVATE not in dumped and API_TOKEN not in dumped and PII_KEY not in dumped
        assert result.provenance.source_url == ORIGIN + path.removeprefix("/api")
    invoices = seen[0].url.params
    assert dict(invoices) == {
        "Top": "10",
        "Skip": "20",
        "IssueDateFrom": "2026-01-01",
        "IssueDateTo": "2026-03-31",
        "PaymentStatus": "0",
        "DueDateStatus": "2",
        "ExtendedFields": "Items,Payments",
    }
    assert not seen[1].url.params
    assert dict(seen[2].url.params) == {"Top": "50", "Skip": "0", "OrderNumber": "OBJ-1"}
    assert seen[3].url.params["DueDateFrom"] == "2026-01-01"
    assert seen[3].url.params["LastModifiedTimestamp"] == "2026-01-01T00:00:00"
    assert seen[5].url.params["Name"] == "šroub"
    assert seen[5].url.params["OnlyMarkedForEshop"] == "true"
    assert seen[5].url.params["CatalogItemChangedTimestamp"] == "2026-01-01T00:00:00"
    assert "LastModifiedTimestamp" not in seen[5].url.params
    assert seen[6].url.params["AccountId"] == "3"
    assert seen[6].url.params["PaymentDateFrom"] == "2026-02-01"
    assert not seen[7].url.params
    assert not any(request.method != "GET" for request in seen)
    assert not any("batch" in request.url.path or "single" in request.url.path for request in seen)


@pytest.mark.anyio
async def test_test_connection_reads_numbering_sequences() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": 1, "name": "FA"}]})

    service = KrosService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/api/numberingSequences")]


@pytest.mark.anyio
async def test_provider_errors_fail_closed_without_leaking() -> None:
    cases: list[tuple[httpx.Response, ErrorCode]] = [
        (httpx.Response(401, json={"message": PRIVATE}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(402, json={"message": PRIVATE}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(403, json={"message": PRIVATE}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"message": PRIVATE}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json={"message": PRIVATE}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text=PRIVATE), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"<html>" + PRIVATE.encode()), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"errors": [PRIVATE]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[{"id": 1}]), ErrorCode.UPSTREAM_ERROR),
        (
            httpx.Response(200, json={"data": [], "errors": {"x": PRIVATE}}),
            ErrorCode.UPSTREAM_ERROR,
        ),
    ]
    current: list[httpx.Response] = []
    service = KrosService(transport=httpx.MockTransport(lambda _: current[0]))
    spec = build_definition(service).tools["get_invoice"]
    for response, code in cases:
        current[:] = [response]
        with pytest.raises(ConnectorError) as caught:
            await spec.handler(spec.input_model.model_validate({"invoice_id": 5}), context())
        assert caught.value.code is code
        assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"secret_ref": f"{SLUG}/other/install-1"}, ErrorCode.FORBIDDEN),
        ({"secret_ref": "other/workspace-1/install-1"}, ErrorCode.FORBIDDEN),
        ({"secret_version": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": {}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"pii_key": "short"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_token": ""}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_token": "short"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_token": "token with spaces 01234"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_token": "token\r\nX-Evil: 01234"}}, ErrorCode.CREDENTIAL_INVALID),
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_are_rejected_before_any_request(
    changes: dict[str, Any], code: ErrorCode
) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": []})

    service = KrosService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_invoices"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context(**changes))
    assert caught.value.code is code
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code is code
    assert calls == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"url": "https://outside.invalid"},
        {"top": 0},
        {"top": 101},
        {"skip": -1},
        {"top": "5"},
        {"top": True},
        {"issue_date_from": "2026-1-1"},
        {"issue_date_from": "2026-02-30"},
        {"issue_date_from": "2026-03-01", "issue_date_to": "2026-02-01"},
        {"last_modified_from": "2026-01-01"},
        {"last_modified_from": "2026-01-01T25:00:00"},
        {"payment_status": "paid"},
        {"payment_status": 1},
        {"due_date_status": "late"},
        {"numbering_sequence": "TOOLONG"},
        {"document_number_from": ""},
        {"order_number": "x" * 21},
        {"extended_fields": ["Partner"]},
        {"extended_fields": "Items"},
        {"extended_fields": ["Items"] * 5},
        {"name": "evil\r\nX: 1"},
        [],
        "text",
        None,
    ],
)
def test_list_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.InvoiceList.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        ({}, s.InvoiceID),
        ({"invoice_id": 0}, s.InvoiceID),
        ({"invoice_id": "5"}, s.InvoiceID),
        ({"invoice_id": 2**53}, s.InvoiceID),
        ({"invoice_id": 1, "x": 1}, s.InvoiceID),
        ({"expense_id": "5"}, s.ExpenseID),
        ({"expense_id": "3F2504E0-4F89-11D3-9A0C-0305E82C330"}, s.ExpenseID),
        ({"expense_id": "../3F2504E0-4F89-11D3-9A0C-0305E82C3301"}, s.ExpenseID),
        ({"expense_id": EXPENSE, "top": 1}, s.ExpenseID),
        ({"top": 1}, s.Empty),
        ({"item_code": "x" * 26}, s.CatalogItemList),
        ({"only_marked_for_eshop": "true"}, s.CatalogItemList),
        ({"account_id": 0}, s.PaymentList),
        ({"external_id": ""}, s.PaymentList),
        ({"delivery_date_from": "2026-01-01"}, s.ProformaInvoiceList),
        ({"due_date_from": "2026-01-01"}, s.InvoiceList),
    ],
)
def test_other_arguments_are_closed(arguments: tuple[Any, type[s.Input]]) -> None:
    values, model = arguments
    with pytest.raises(ValidationError):
        model.model_validate(values)
    assert s.InvoiceList.model_validate({}).top == 50
    assert s.ExpenseID.model_validate({"expense_id": EXPENSE}).expense_id == EXPENSE
