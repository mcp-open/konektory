from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_vyfakturuj.app import build_definition
from connector_vyfakturuj.service import SLUG, VyfakturujService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
EMAIL = "synthetic@example.test"
API_KEY = "synthetic-private-api-key-0123456789"
INVOICE = {
    "id": 12436464,
    "number": "20260004",
    "type": 1,
    "flags": 3,
    "customer_name": "Private Customer s.r.o.",
    "customer_tel": "+420000000000",
    "total": "1210.00",
}
CONTACT = {"id": 3541748, "IC": "12345678", "name": "Private Person", "mail_to": EMAIL}


def context(**changes: Any) -> InvocationContext:
    values = {"email": EMAIL, "api_key": API_KEY, "pii_key": PII_KEY}
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


CASES: dict[str, tuple[dict[str, Any], str, bool]] = {
    "list_invoices": (
        {
            "type": 1,
            "flags": 2,
            "id_customer": 7,
            "variable_symbol": "20260004",
            "currency": "CZK",
            "date_created_from": "2026-01-01",
            "date_created_to": "2026-03-31",
            "q": "audit",
            "rows_limit": 2,
            "rows_offset": 4,
            "sort_by": "date_created",
        },
        "/2.0/invoice/",
        True,
    ),
    "get_invoice": ({"invoice_id": 12436464}, "/2.0/invoice/12436464/", False),
    "list_contacts": (
        {"ic": "12345678", "sort_by": "name", "sort_dir": "desc"},
        "/2.0/contact/",
        True,
    ),
    "get_contact": ({"contact_id": 3541748}, "/2.0/contact/3541748/", False),
    "get_template": ({"template_id": 123831}, "/2.0/template/123831/", False),
    "list_payment_methods": ({}, "/2.0/settings/payment-method/", True),
    "list_number_series": ({}, "/2.0/settings/number-series/", True),
    "list_tags": ({}, "/2.0/settings/tags/", True),
}


@pytest.mark.anyio
async def test_all_tools_use_documented_get_paths_and_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/contact/"):
            return httpx.Response(200, json=[CONTACT, CONTACT])
        if path.startswith("/2.0/contact/"):
            return httpx.Response(200, json=CONTACT)
        if path.startswith("/2.0/settings/"):
            return httpx.Response(200, json=[{"id_tag": 1, "name": "Private Tag"}])
        return httpx.Response(
            200, json=[INVOICE, INVOICE] if path.endswith("/invoice/") else INVOICE
        )

    definition = build_definition(VyfakturujService(transport=httpx.MockTransport(upstream)))
    assert set(definition.tools) == set(CASES)
    for name, (arguments, path, collection) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.scheme == "https" and request.url.host == "api.vyfakturuj.cz"
        assert request.url.path == path
        assert request.headers["authorization"].startswith("Basic ")
        assert request.headers["user-agent"].startswith("OpenMCP/")
        assert request.headers["accept"] == "application/json"
        text = result.model_dump_json()
        for secret in ("Private", API_KEY, EMAIL, PII_KEY, "+420000000000"):
            assert secret not in text
        assert result.provenance.source_url.startswith("https://api.vyfakturuj.cz/2.0/")
        assert "?" not in result.provenance.source_url
        if collection:
            assert result.data["count"] in (1, 2)
            assert isinstance(result.data["items"], list)
    params = seen[0].url.params
    assert params["type"] == "1" and params["flags"] == "2" and params["id_customer"] == "7"
    assert params["VS"] == "20260004" and "variable_symbol" not in params
    assert params["currency"] == "CZK" and params["q"] == "audit"
    assert params["date_created_from"] == "2026-01-01"
    assert params["date_created_to"] == "2026-03-31"
    assert params["rows_limit"] == "2" and params["rows_offset"] == "4"
    assert params["sort"] == "date_created~desc" and "sort_by" not in params
    contact_params = seen[2].url.params
    assert contact_params["IC"] == "12345678" and "ic" not in contact_params
    assert contact_params["sort"] == "name~desc"
    assert contact_params["rows_limit"] == "20" and contact_params["rows_offset"] == "0"
    assert all(request.url.path.startswith("/2.0/") for request in seen)


@pytest.mark.anyio
async def test_truncated_flag_and_safe_test_endpoint() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/2.0/test/":
            return httpx.Response(200, json={"method": "GET", "message": "Welcome"})
        return httpx.Response(200, json=[INVOICE, INVOICE])

    service = VyfakturujService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/2.0/test/" and seen[0].method == "GET"
    spec = build_definition(service).tools["list_invoices"]
    full = await spec.handler(spec.input_model.model_validate({"rows_limit": 2}), context())
    assert full.data["truncated"] is True and full.data["count"] == 2
    more = await spec.handler(spec.input_model.model_validate({"rows_limit": 3}), context())
    assert more.data["truncated"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"email": "not-an-email"}},
        {"credentials": {"email": "bad:mail@example.test"}},
        {"credentials": {"email": "a@b.test/x"}},
        {"credentials": {"api_key": "short"}},
        {"credentials": {"api_key": "with space 0123456789"}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_version": None},
        {"provider_credential": None},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = VyfakturujService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    spec = build_definition(service).tools["list_tags"]
    with pytest.raises(ConnectorError):
        await spec.handler(spec.input_model.model_validate({}), context(**changes))
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (
            httpx.Response(400, json={"status": "error", "message": "private validation"}),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (
            httpx.Response(401, json={"status": "error", "message": "private"}),
            ErrorCode.CREDENTIAL_INVALID,
        ),
        (httpx.Response(403, text="private"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"status": "error", "message": "private"}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(503, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
        (
            httpx.Response(200, json={"status": "error", "message": "private"}),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (httpx.Response(200, json={"id": 1}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=["private"]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json="private"), ErrorCode.UPSTREAM_ERROR),
        (
            httpx.Response(302, headers={"Location": "https://outside.invalid/"}),
            ErrorCode.UPSTREAM_ERROR,
        ),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = VyfakturujService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["list_invoices"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


@pytest.mark.anyio
async def test_detail_rejects_list_payload_and_overlarge_response() -> None:
    service = VyfakturujService(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    )
    spec = build_definition(service).tools["get_invoice"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"invoice_id": 1}), context())
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR
    big = b"[" + b",".join(b'{"id":1,"name":"private"}' for _ in range(90_000)) + b"]"
    assert len(big) > 2 * 1024 * 1024
    huge = httpx.MockTransport(lambda r: httpx.Response(200, content=big))
    spec = build_definition(VyfakturujService(transport=huge)).tools["list_tags"]
    with pytest.raises(ConnectorError):
        await spec.handler(spec.input_model.model_validate({}), context())


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_invoices", {"rows_limit": 0}),
        ("list_invoices", {"rows_limit": 101}),
        ("list_invoices", {"rows_offset": -1}),
        ("list_invoices", {"rows_limit": "5"}),
        ("list_invoices", {"rows_limit": True}),
        ("list_invoices", {"type": 3}),
        ("list_invoices", {"type": "1"}),
        ("list_invoices", {"flags": 0}),
        ("list_invoices", {"variable_symbol": "12345678901"}),
        ("list_invoices", {"variable_symbol": "abc"}),
        ("list_invoices", {"currency": "czk"}),
        ("list_invoices", {"date_created_from": "2026-13-01"}),
        ("list_invoices", {"date_created_from": "2026-02-30"}),
        ("list_invoices", {"date_created_from": "01.02.2026"}),
        ("list_invoices", {"date_created_from": "2026-03-01", "date_created_to": "2026-02-01"}),
        ("list_invoices", {"sort_by": "customer_name"}),
        ("list_invoices", {"sort_dir": "random"}),
        ("list_invoices", {"q": ""}),
        ("list_invoices", {"q": "evil\r\nAuthorization: other"}),
        ("list_invoices", {"filter": "id~gt~1"}),
        ("list_invoices", {"url": "https://outside.invalid"}),
        ("get_invoice", {}),
        ("get_invoice", {"invoice_id": 0}),
        ("get_invoice", {"invoice_id": "7"}),
        ("get_invoice", {"invoice_id": True}),
        ("get_invoice", {"invoice_id": 2**53}),
        ("list_contacts", {"ic": "123"}),
        ("list_contacts", {"dic": "cz12345678"}),
        ("list_contacts", {"mail_to": "nomail"}),
        ("list_contacts", {"sort_by": "mail_to"}),
        ("get_contact", {"contact_id": -1}),
        ("get_template", {"template_id": 1.5}),
        ("list_tags", {"limit": 1}),
        ("list_tags", []),
        ("list_tags", "text"),
        ("list_tags", None),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
