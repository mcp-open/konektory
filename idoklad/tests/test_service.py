from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_idoklad import schemas as s
from connector_idoklad.app import build_definition
from connector_idoklad.service import (
    LISTS,
    SLUG,
    TOKEN_PATH,
    FormTransport,
    IdokladService,
    filter_expression,
)

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
CLIENT_ID = "11111111-2222-3333-4444-555555555555"
CLIENT_SECRET = "66666666-7777-8888-9999-000000000000"
APPLICATION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ACCESS_TOKEN = "private-access-token-0123456789abcdef"
PRIVATE = "Private company s.r.o."


def context(**changes: Any) -> InvocationContext:
    values = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "application_id": APPLICATION_ID,
        "pii_key": PII_KEY,
    }
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


def envelope(data: Any) -> dict[str, Any]:
    return {"Data": data, "Message": "", "StatusCode": 200, "ErrorCode": 0}


def token_response(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "identity.idoklad.cz" and request.url.path == TOKEN_PATH
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    form = parse_qs(request.content.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "application_id": [APPLICATION_ID],
        "client_id": [CLIENT_ID],
        "client_secret": [CLIENT_SECRET],
        "scope": ["idoklad_api"],
    }
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "Bearer"})


def make_service(
    handler: Callable[[httpx.Request], httpx.Response],
    seen: list[httpx.Request] | None = None,
) -> tuple[IdokladService, list[httpx.Request]]:
    seen = seen if seen is not None else []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return token_response(request)
        return handler(request)

    return IdokladService(transport=httpx.MockTransport(upstream)), seen


@pytest.mark.anyio
async def test_every_tool_exchanges_token_then_gets_documented_path() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=envelope(
                {
                    "Items": [{"Id": 7, "CompanyName": PRIVATE, "DocumentNumber": "20260001"}],
                    "TotalItems": 1,
                    "TotalPages": 1,
                }
            ),
        )

    service, seen = make_service(upstream)
    definition = build_definition(service)
    cases = {
        "list_issued_invoices": ({}, "/v3/IssuedInvoices"),
        "get_issued_invoice": ({"invoice_id": 7}, "/v3/IssuedInvoices/7"),
        "list_received_invoices": ({"page": 2}, "/v3/ReceivedInvoices"),
        "list_contacts": ({"sort_by": "CompanyName", "sort_order": "desc"}, "/v3/Contacts"),
        "get_contact": ({"contact_id": 7}, "/v3/Contacts/7"),
        "list_bank_statements": ({"page_size": 100}, "/v3/BankStatements"),
        "list_issued_payments": ({}, "/v3/IssuedDocumentPayments"),
        "get_current_agenda": ({}, "/v3/Account/CurrentAgenda"),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only
        token, get = seen[-2], seen[-1]
        assert token.method == "POST" and token.url.path == TOKEN_PATH
        assert get.method == "GET" and get.url.host == "api.idoklad.cz"
        assert get.url.path == path
        assert get.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert get.headers["user-agent"].startswith("OpenMCP/")
        assert "authorization" not in token.headers
        dumped = result.model_dump_json()
        assert PRIVATE not in dumped
        for secret in (CLIENT_SECRET, ACCESS_TOKEN, PII_KEY):
            assert secret not in dumped
        assert result.data["Items"][0]["Id"] == 7
    assert seen[1].url.params["page"] == "1"
    assert seen[1].url.params["pagesize"] == "20"
    assert "filter" not in seen[1].url.params and "sort" not in seen[1].url.params
    assert seen[5].url.params["page"] == "2"
    assert seen[7].url.params["sort"] == "CompanyName~desc"
    assert seen[11].url.params["pagesize"] == "100"
    assert not seen[3].url.params
    # The token is obtained per invocation; nothing is reused between calls.
    assert sum(1 for request in seen if request.method == "POST") == len(cases)


@pytest.mark.anyio
async def test_filters_use_documented_syntax_and_only_allowed_columns() -> None:
    service, seen = make_service(lambda _: httpx.Response(200, json=envelope({"Items": []})))
    spec = build_definition(service).tools["list_issued_invoices"]
    arguments = spec.input_model.model_validate(
        {
            "filters": [
                {"field": "DateOfIssue", "operator": "gte", "value": "2026-01-01"},
                {"field": "PartnerId", "value": "42"},
                {"field": "Description", "operator": "ct", "value": "servis auta"},
                {"field": "TagIds", "operator": "ct", "value": "854,855"},
            ],
            "filter_type": "or",
        }
    )
    await spec.handler(arguments, context())
    encoded = base64.b64encode(b"servis auta").decode()
    assert seen[-1].url.params["filter"] == (
        f"(DateOfIssue~gte~2026-01-01~and~PartnerId~eq~42~and~Description~ct:base64~{encoded}"
        "~and~TagIds~ct~854,855)"
    )
    assert seen[-1].url.params["filtertype"] == "or"
    calls = len(seen)
    for bad in (
        {"filters": [{"field": "CompanyName", "value": "x"}]},
        {"filters": [{"field": "Prices", "value": "1"}]},
        {"filters": [{"field": "DateOfIssue", "operator": "gt", "value": "a b"}]},
        {"sort_by": "PartnerId"},
    ):
        with pytest.raises(ConnectorError) as caught:
            await spec.handler(spec.input_model.model_validate(bad), context())
        assert caught.value.code is ErrorCode.INVALID_INPUT
    assert len(seen) == calls
    for name, (_, columns, sorts) in LISTS.items():
        assert "Id" in columns and "Id" in sorts, name
    assert filter_expression([{"field": "Id", "operator": "eq", "value": "1"}], frozenset({"Id"}))


@pytest.mark.anyio
async def test_post_is_only_ever_the_token_exchange() -> None:
    service, seen = make_service(lambda _: httpx.Response(200, json=envelope({"Id": 1})))
    definition = build_definition(service)
    for name, spec in definition.tools.items():
        arguments = {"invoice_id": 1} if name == "get_issued_invoice" else {}
        if name == "get_contact":
            arguments = {"contact_id": 1}
        await spec.handler(spec.input_model.model_validate(arguments), context())
    await service.test_connection(context())
    posts = [request for request in seen if request.method != "GET"]
    assert posts and all(
        request.method == "POST"
        and request.url.host == "identity.idoklad.cz"
        and request.url.path == TOKEN_PATH
        for request in posts
    )
    assert all(request.url.host == "api.idoklad.cz" for request in seen if request.method == "GET")
    assert all(request.url.path.startswith("/v3/") for request in seen if request.method == "GET")
    assert not any("Create" in request.url.path or "Copy" in request.url.path for request in seen)
    # The form transport itself refuses anything but the documented token POST.
    transport = FormTransport(httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    for request in (
        httpx.Request("POST", "https://identity.idoklad.cz/server/connect/token", json={}),
        httpx.Request("GET", f"https://identity.idoklad.cz{TOKEN_PATH}"),
        httpx.Request("POST", "https://identity.idoklad.cz/v3/IssuedInvoices", json={}),
    ):
        with pytest.raises(ConnectorError):
            await transport.handle_async_request(request)


@pytest.mark.anyio
async def test_token_failures_map_to_credential_errors_without_leaking() -> None:
    responses = iter(
        [
            httpx.Response(400, json={"error": "invalid_client", "error_description": PRIVATE}),
            httpx.Response(200, json={"error": "invalid_scope", "detail": PRIVATE}),
            httpx.Response(200, json={"access_token": "short", "token_type": "Bearer"}),
            httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "MAC"}),
            httpx.Response(401, text=PRIVATE),
        ]
    )
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        return next(responses)

    service = IdokladService(transport=httpx.MockTransport(upstream))
    for _ in range(5):
        with pytest.raises(ConnectorError) as caught:
            await service.test_connection(context())
        assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
        assert PRIVATE not in str(caught.value)
    assert len(seen) == 5


@pytest.mark.anyio
async def test_provider_errors_fail_closed_without_leaking() -> None:
    cases: list[tuple[httpx.Response, ErrorCode]] = [
        (httpx.Response(401, json={"Message": PRIVATE}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, json={"Message": PRIVATE}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"Message": PRIVATE}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json={"Message": PRIVATE}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text=PRIVATE), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"<html>" + PRIVATE.encode()), ErrorCode.UPSTREAM_ERROR),
        (
            httpx.Response(200, json={"Data": None, "Message": PRIVATE, "ErrorCode": 126}),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (httpx.Response(200, json=[{"Id": 1}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"Items": []}), ErrorCode.UPSTREAM_ERROR),
    ]
    current: list[httpx.Response] = []
    service, _ = make_service(lambda _: current[0])
    spec = build_definition(service).tools["get_issued_invoice"]
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
        ({"credentials": {"client_id": ""}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"client_secret": "bad secret"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"application_id": "x"}}, ErrorCode.CREDENTIAL_INVALID),
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
        return httpx.Response(200, json={})

    service = IdokladService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_contacts"]
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
        {"page": 0},
        {"page": 10_001},
        {"page_size": 0},
        {"page_size": 101},
        {"page": "1"},
        {"page": True},
        {"filter_type": "xor"},
        {"sort_order": "up"},
        {"sort_by": ""},
        {"sort_by": "Id~desc"},
        {"filters": [{"field": "Id"}]},
        {"filters": [{"field": "Id", "value": ""}]},
        {"filters": [{"field": "Id", "operator": "like", "value": "1"}]},
        {"filters": [{"field": "Id", "value": "1", "extra": 1}]},
        {"filters": [{"field": "Id", "value": "evil\r\nx"}]},
        {"filters": [{"field": "Id", "value": "1"}] * 9},
        {"filters": "Id~eq~1"},
        [],
        "text",
        None,
    ],
)
def test_list_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.IssuedInvoiceList.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [{}, {"invoice_id": 0}, {"invoice_id": "5"}, {"invoice_id": 2**53}, {"invoice_id": 1, "x": 1}],
)
def test_detail_arguments_are_closed(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.IssuedInvoiceID.model_validate(arguments)
    with pytest.raises(ValidationError):
        s.Empty.model_validate({"include": "Items"})
    assert s.IssuedInvoiceList.model_validate({}).page_size == 20
