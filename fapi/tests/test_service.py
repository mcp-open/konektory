from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_fapi import schemas as s
from connector_fapi.app import build_definition
from connector_fapi.service import ORIGIN, SLUG, FapiService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
USERNAME = "synthetic@example.test"
API_KEY = "synthetic-fapi-api-key-0123456789abcdef"
PRIVATE = "Private customer"


def context(**changes: Any) -> InvocationContext:
    values = {"username": USERNAME, "api_key": API_KEY, "pii_key": PII_KEY}
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


def expected_basic() -> str:
    return "Basic " + base64.b64encode(f"{USERNAME}:{API_KEY}".encode()).decode()


@pytest.mark.anyio
async def test_every_tool_gets_documented_path_with_basic_auth() -> None:
    seen: list[httpx.Request] = []
    collections = {
        "/invoices": "invoices",
        "/clients": "clients",
        "/forms": "forms",
        "/item_templates": "item_templates",
        "/payments": "payments",
    }

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        record = {"id": 7, "number": "20260001", "customer_name": PRIVATE, "total": "100.00"}
        collection = collections.get(request.url.path)
        return httpx.Response(200, json={collection: [record]} if collection else record)

    definition = build_definition(FapiService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_invoices": (
            {
                "status": "paid",
                "client": 4,
                "create_date_from": "2026-01-01",
                "create_date_to": "2026-01-31",
                "order_by": "paid_on",
                "limit": 50,
                "offset": 100,
                "search": "audit",
            },
            "/invoices",
        ),
        "get_invoice": ({"invoice_id": 7}, "/invoices/7"),
        "list_clients": ({"email": "x@example.test", "show_statistics": True}, "/clients"),
        "get_client": ({"client_id": 7, "show_statistics": True}, "/clients/7"),
        "list_forms": ({"show_deleted": False, "order_by": "name"}, "/forms"),
        "get_form": ({"form_id": 7, "with_payment_methods": True}, "/forms/7"),
        "list_item_templates": ({"code": "SKU-1", "form": 3}, "/item_templates"),
        "list_payments": ({"date": "2026-02-01", "unpaired": True}, "/payments"),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].method == "GET"
        assert seen[-1].url.host == "api.fapi.cz" and seen[-1].url.path == path
        assert seen[-1].headers["authorization"] == expected_basic()
        assert seen[-1].headers["user-agent"].startswith("OpenMCP/")
        assert seen[-1].headers["accept"] == "application/json"
        dumped = result.model_dump_json()
        assert PRIVATE not in dumped and API_KEY not in dumped and PII_KEY not in dumped
        assert USERNAME not in dumped
        assert result.provenance.source_url == ORIGIN + path
    assert dict(seen[0].url.params) == {
        "create_date[0]": "2026-01-01",
        "create_date[1]": "2026-01-31",
        "order": "paid_on",
        "limit": "50",
        "offset": "100",
        "status": "paid",
        "client": "4",
        "search": "audit",
    }
    assert not seen[1].url.params
    assert dict(seen[2].url.params) == {
        "limit": "20",
        "offset": "0",
        "email": "x@example.test",
        "show_statistics": "true",
    }
    assert dict(seen[3].url.params) == {"show_statistics": "true"}
    assert dict(seen[4].url.params) == {"limit": "20", "offset": "0", "order": "name"}
    assert dict(seen[5].url.params) == {"with_payment_methods": "true"}
    assert seen[6].url.params["code"] == "SKU-1" and seen[6].url.params["form"] == "3"
    assert seen[7].url.params["date"] == "2026-02-01" and seen[7].url.params["unpaired"] == "true"
    assert all(request.method == "GET" for request in seen)


@pytest.mark.anyio
async def test_half_open_date_range_is_rejected_before_any_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"invoices": []})

    spec = build_definition(FapiService(transport=httpx.MockTransport(upstream))).tools[
        "list_invoices"
    ]
    for arguments in ({"paid_on_from": "2026-01-01"}, {"payday_date_to": "2026-01-01"}):
        with pytest.raises(ConnectorError) as caught:
            await spec.handler(spec.input_model.model_validate(arguments), context())
        assert caught.value.code is ErrorCode.INVALID_INPUT
    assert calls == 0


@pytest.mark.anyio
async def test_test_connection_reads_current_user() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 1, "username": USERNAME, "plan": "START"})

    service = FapiService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/user")]


@pytest.mark.anyio
async def test_provider_errors_fail_closed_without_leaking() -> None:
    error = {"message": PRIVATE, "type": "AuthorizationException"}
    cases: list[tuple[httpx.Response, ErrorCode]] = [
        (httpx.Response(400, json=error), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json=error), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, json=error), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json=error), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json=error), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text=PRIVATE), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"<html>" + PRIVATE.encode()), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=error), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[{"id": 1}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"clients": [PRIVATE]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"invoices": {"id": 1}}), ErrorCode.UPSTREAM_ERROR),
    ]
    current: list[httpx.Response] = []
    service = FapiService(transport=httpx.MockTransport(lambda _: current[0]))
    spec = build_definition(service).tools["list_invoices"]
    for response, code in cases:
        current[:] = [response]
        with pytest.raises(ConnectorError) as caught:
            await spec.handler(spec.input_model.model_validate({}), context())
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
        ({"credentials": {"username": ""}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"username": "bad:user@example.test"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": "short"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": "key with spaces 0123456789"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": "key\r\nX-Evil: 01234"}}, ErrorCode.CREDENTIAL_INVALID),
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
        return httpx.Response(200, json={"clients": []})

    service = FapiService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_clients"]
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
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"limit": "5"},
        {"limit": True},
        {"status": "open"},
        {"type": "Invoice"},
        {"type": "invoice; drop"},
        {"number": ""},
        {"number": "x" * 65},
        {"variable_symbol": "12a"},
        {"client": 0},
        {"client": "4"},
        {"search": ""},
        {"search": "evil\r\nX: 1"},
        {"create_date_from": "2026-1-1"},
        {"create_date_from": "2026-02-30"},
        {"create_date_from": "2026-03-01", "create_date_to": "2026-02-01"},
        {"last_modified_after": "2026-01-01"},
        {"last_modified_after": "2026-01-01 25:00:00"},
        {"order_by": "id desc"},
        {"order_by": "customer_email"},
        {"client_email": "x@example.test"},
        [],
        "text",
        None,
    ],
)
def test_invoice_arguments_are_closed_and_bounded(arguments: Any) -> None:
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
        ({"client_id": 1, "show_statistics": "true"}, s.ClientID),
        ({"email": "not-an-email"}, s.ClientList),
        ({"order_by": "company"}, s.ClientList),
        ({"form_id": 1, "with_payment_methods": 1}, s.FormID),
        ({"order_by": "path"}, s.FormList),
        ({"code": "../x"}, s.ItemTemplateList),
        ({"name": ""}, s.ItemTemplateList),
        ({"date": "2026-13-01"}, s.PaymentList),
        ({"unpaired": "yes"}, s.PaymentList),
        ({"hidden": True}, s.PaymentList),
    ],
)
def test_other_arguments_are_closed(arguments: tuple[Any, type[s.Input]]) -> None:
    values, model = arguments
    with pytest.raises(ValidationError):
        model.model_validate(values)
    assert s.InvoiceList.model_validate({}).limit == 20
    assert s.ClientList.model_validate({}).show_statistics is False
