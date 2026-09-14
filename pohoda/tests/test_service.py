from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_pohoda.app import build_definition
from connector_pohoda.service import (
    NS,
    READ_REQUESTS,
    XML_PATH,
    PohodaService,
    XmlTransport,
    build_request,
    mserver_origin,
    parse_response,
)

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
MSERVER = "https://pohoda.example.test:8443"
USERNAME = "synthetic-user"
PASSWORD = "synthetic-private-password"
ICO = "12345678"
RSP = NS["rsp"]
LST = NS["lst"]


def response_pack(list_tag: str, record_tag: str, records: str, state: str = "ok") -> bytes:
    prefix = {"listAddressBook": "lAdb", "listStock": "lStk"}.get(list_tag, "lst")
    text = (
        '<?xml version="1.0" encoding="Windows-1250"?>'
        f'<rsp:responsePack version="2.0" id="openmcp" state="{state}" '
        f'programVersion="14000.1" ico="{ICO}" '
        f'note="Private note" xmlns:rsp="{RSP}" xmlns:lst="{LST}" xmlns:lAdb="{NS["lAdb"]}" '
        f'xmlns:lStk="{NS["lStk"]}" xmlns:typ="{NS["typ"]}">'
        f'<rsp:responsePackItem version="2.0" id="openmcp-1" state="{state}">'
        f'<{prefix}:{list_tag} version="2.0" dateTimeStamp="2024-01-01T10:00:00" '
        f'dateValidFrom="2024-01-01" state="ok">'
        f"{records}</{prefix}:{list_tag}></rsp:responsePackItem></rsp:responsePack>"
    )
    return text.encode("cp1250")


INVOICES = response_pack(
    "listInvoice",
    "invoice",
    '<lst:invoice version="2.0"><inv:invoiceHeader xmlns:inv="x"><inv:id>7</inv:id><inv:number>'
    "<typ:numberRequested>240100001</typ:numberRequested></inv:number><inv:partnerIdentity>"
    "<typ:address><typ:company>Private Novák s.r.o.</typ:company></typ:address>"
    "</inv:partnerIdentity></inv:invoiceHeader></lst:invoice>"
    '<lst:invoice version="2.0"><inv:invoiceHeader xmlns:inv="x"><inv:id>8</inv:id>'
    "</inv:invoiceHeader></lst:invoice>",
)
EMPTY = response_pack("listInvoice", "invoice", "")


def context(**changes: Any) -> InvocationContext:
    values = {
        "mserver_url": MSERVER,
        "username": USERNAME,
        "password": PASSWORD,
        "ico": ICO,
        "pii_key": PII_KEY,
    }
    values.update(changes.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="subject-1",
        workspace_id="workspace-1",
        installation_id="install-1",
        secret_ref="pohoda/workspace-1/install-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=changes)


def reply_for(request: httpx.Request) -> httpx.Response:
    root = ET.fromstring(request.content)
    name = root[0][0].tag.rsplit("}", 1)[-1]
    listing = {
        "listInvoiceRequest": ("listInvoice", "invoice"),
        "listOrderRequest": ("listOrder", "order"),
        "listAddressBookRequest": ("listAddressBook", "addressbook"),
        "listStockRequest": ("listStock", "stock"),
    }[name]
    prefix = {"listAddressBook": "lAdb", "listStock": "lStk"}.get(listing[0], "lst")
    body = response_pack(
        listing[0],
        listing[1],
        f'<{prefix}:{listing[1]} version="2.0"><typ:id>7</typ:id><typ:name>Private Novák</typ:name>'
        f"</{prefix}:{listing[1]}>",
    )
    return httpx.Response(
        200, content=body, headers={"Content-Type": "text/xml; charset=Windows-1250"}
    )


CASES: dict[str, tuple[dict[str, Any], str, dict[str, str], list[str]]] = {
    "list_invoices": (
        {
            "invoice_type": "receivedInvoice",
            "date_from": "2024-01-01",
            "date_till": "2024-03-31",
            "company": "Novák & syn",
            "ico": "87654321",
            "last_changes": "2024-02-01T00:00:00",
            "count": 25,
            "id_from": 100,
        },
        "listInvoiceRequest",
        {"invoiceType": "receivedInvoice", "invoiceVersion": "2.0"},
        ["dateFrom", "dateTill", "selectedCompanys", "selectedIco", "lastChanges"],
    ),
    "get_invoice": (
        {"invoice_id": 7},
        "listInvoiceRequest",
        {"invoiceType": "issuedInvoice"},
        ["id"],
    ),
    "list_orders": (
        {"order_type": "issuedOrder"},
        "listOrderRequest",
        {"orderType": "issuedOrder", "orderVersion": "2.0"},
        [],
    ),
    "get_order": ({"order_id": 7}, "listOrderRequest", {"orderType": "receivedOrder"}, ["id"]),
    "list_partners": (
        {"company": "Novák", "city": "Brno"},
        "listAddressBookRequest",
        {"addressBookVersion": "2.0"},
        ["company", "city"],
    ),
    "get_partner": ({"partner_id": 7}, "listAddressBookRequest", {}, ["id"]),
    "list_stock": (
        {"code": "SKU-1", "ean": "8594001234567"},
        "listStockRequest",
        {"stockVersion": "2.0"},
        ["code", "EAN"],
    ),
    "get_stock_item": ({"stock_id": 7}, "listStockRequest", {}, ["id"]),
}


@pytest.mark.anyio
async def test_all_tools_post_only_allow_listed_list_requests() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply_for(request)

    definition = build_definition(PohodaService(transport=httpx.MockTransport(upstream)))
    assert set(definition.tools) == set(CASES)
    expected_auth = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    for name, (arguments, request_name, attributes, filter_tags) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "POST"
        assert str(request.url) == f"{MSERVER}{XML_PATH}"
        assert request.headers["stw-authorization"] == expected_auth
        assert request.headers["stw-application"] == "OpenMCP"
        assert "authorization" not in request.headers
        assert request.headers["content-type"].startswith("text/xml")
        assert request.content.startswith(b"<?xml version='1.0' encoding='windows-1250'?>")
        root = ET.fromstring(request.content)
        assert root.tag == f"{{{NS['dat']}}}dataPack"
        assert root.attrib["ico"] == ICO and root.attrib["version"] == "2.0"
        assert root.attrib["application"] == "OpenMCP"
        items = list(root)
        assert len(items) == 1 and items[0].tag == f"{{{NS['dat']}}}dataPackItem"
        request_element = items[0][0]
        assert request_element.tag.rsplit("}", 1)[-1] == request_name
        assert request_name in READ_REQUESTS and request_name.endswith("Request")
        assert request_element.attrib["version"] == "2.0"
        for key, value in attributes.items():
            assert request_element.attrib[key] == value
        filters = [
            e.tag.rsplit("}", 1)[-1]
            for e in request_element.iter()
            if e.tag.startswith(f"{{{NS['ftr']}}}")
        ]
        for tag in filter_tags:
            assert tag in filters
        assert "queryFilter" not in filters and "userFilterName" not in filters
        text = result.model_dump_json()
        for secret in ("Private", "Nov", PASSWORD, USERNAME, PII_KEY, "pohoda.example.test"):
            assert secret not in text
        assert result.provenance.source_url == "https://{mserver_url}/xml"
        if name.startswith("list_"):
            assert result.data["count"] == 1 and len(result.data["items"]) == 1
        else:
            assert isinstance(result.data["data"], dict)
    first = ET.fromstring(seen[0].content)[0][0]
    limit = first.find(f"{{{LST}}}limit")
    assert limit is not None
    assert limit.find(f"{{{LST}}}idFrom").text == "100"  # type: ignore[union-attr]
    assert limit.find(f"{{{LST}}}count").text == "25"  # type: ignore[union-attr]
    holder = first.find(f"{{{LST}}}requestInvoice")
    assert holder is not None
    ftr = f"{{{NS['ftr']}}}"
    assert holder.find(f"{ftr}filter/{ftr}dateFrom").text == "2024-01-01"  # type: ignore[union-attr]
    assert holder.find(f"{ftr}filter/{ftr}selectedCompanys/{ftr}company").text == "Novák & syn"  # type: ignore[union-attr]
    assert holder.find(f"{ftr}filter/{ftr}selectedIco/{ftr}ico").text == "87654321"  # type: ignore[union-attr]
    detail = ET.fromstring(seen[1].content)[0][0]
    assert detail.find(f"{{{LST}}}limit/{{{LST}}}count").text == "1"  # type: ignore[union-attr]
    assert detail.find(f"{{{LST}}}requestInvoice/{ftr}filter/{ftr}id").text == "7"  # type: ignore[union-attr]
    assert seen[0].content.count(b"Nov\xe1k") == 1  # Windows-1250 body
    assert all(request.url.path == XML_PATH for request in seen)


@pytest.mark.anyio
async def test_truncated_flag_detail_not_found_and_safe_test() -> None:
    reply: dict[str, bytes] = {"body": INVOICES}
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=reply["body"])

    service = PohodaService(transport=httpx.MockTransport(upstream))
    definition = build_definition(service)
    spec = definition.tools["list_invoices"]
    result = await spec.handler(spec.input_model.model_validate({"count": 2}), context())
    assert result.data["count"] == 2 and result.data["truncated"] is True
    header = next(v for v in result.data["items"][0].values() if isinstance(v, dict))
    assert header["id"] == "7"  # numeric id survives; other keys/values are pseudonymised
    result = await spec.handler(spec.input_model.model_validate({"count": 3}), context())
    assert result.data["truncated"] is False
    reply["body"] = EMPTY
    result = await spec.handler(spec.input_model.model_validate({}), context())
    assert result.data == {"items": [], "count": 0, "truncated": False}
    spec = definition.tools["get_invoice"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"invoice_id": 7}), context())
    assert caught.value.code is ErrorCode.NOT_FOUND
    reply["body"] = response_pack("listAddressBook", "addressbook", "")
    assert await service.test_connection(context()) == {"connected": True}
    root = ET.fromstring(seen[-1].content)
    assert root[0][0].tag == f"{{{NS['lAdb']}}}listAddressBookRequest"
    assert root[0][0].attrib["addressBookVersion"] == "2.0"


@pytest.mark.anyio
async def test_non_read_requests_are_refused_before_egress() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=INVOICES)

    service = PohodaService(transport=httpx.MockTransport(upstream))
    for name in ("invoice", "listUserCodeRequest", "deleteOrder", "listInvoice", "", "print"):
        with pytest.raises(ConnectorError) as caught:
            await service.xml(context(), name, record_type="issuedInvoice", limit={"count": 1})
        assert caught.value.code is ErrorCode.INTERNAL
    # type attribute mismatch with the request family is refused as well
    with pytest.raises(ConnectorError):
        await service.xml(context(), "listStockRequest", record_type="issuedInvoice")
    with pytest.raises(ConnectorError):
        await service.xml(context(), "listInvoiceRequest")
    for body in (
        {"request": "invoice", "ico": ICO},
        {"request": "listInvoiceRequest", "ico": "bad"},
        {"request": ["listInvoiceRequest"], "ico": ICO},
        {},
    ):
        with pytest.raises(ConnectorError):
            build_request(body)
    transport = XmlTransport(httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await transport.handle_async_request(
            httpx.Request(
                "POST", f"{MSERVER}/xml", content=b'{"request": "invoice", "ico": "12345678"}'
            )
        )
    with pytest.raises(ConnectorError):
        await transport.handle_async_request(
            httpx.Request(
                "GET",
                f"{MSERVER}/status",
                content=b'{"request": "listStockRequest", "ico": "12345678"}',
            )
        )
    with pytest.raises(ConnectorError):
        await transport.handle_async_request(
            httpx.Request(
                "POST",
                f"{MSERVER}/documents",
                content=b'{"request": "listStockRequest", "ico": "12345678"}',
            )
        )
    await transport.aclose()
    with pytest.raises(ConnectorError):
        await service.invoke(
            "import_invoice",
            build_definition().tools["list_stock"].input_model.model_validate({}),
            context(),
        )
    assert calls == 0


@pytest.mark.parametrize(
    "raw",
    [
        "https://pohoda.example.test",
        "https://pohoda.example.test/",
        "https://pohoda.example.test:8443",
        "https://192.0.2.10:444",
        "https://mserver",
    ],
)
def test_valid_mserver_origins_are_normalised(raw: str) -> None:
    origin = mserver_origin(raw)
    assert origin.startswith("https://") and not origin.endswith("/")
    assert origin == raw.rstrip("/")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://pohoda.example.test",
        "pohoda.example.test:444",
        "https://pohoda.example.test/xml",
        "https://pohoda.example.test/x/",
        "https://pohoda.example.test?x=1",
        "https://pohoda.example.test#frag",
        "https://user:pw@pohoda.example.test",
        "https://user@pohoda.example.test",
        "https://[::1]:444",
        "https://pohoda.example.test:0",
        "https://pohoda.example.test:70000",
        "https://pohoda.example.test:abc",
        "https://pohoda.example.test:",
        " https://pohoda.example.test",
        "https://pohoda.example.test\r\nHost: evil",
        "https://pohoda.example.test\\x",
        "https://pohoda example.test",
        "https://-bad.example.test",
        "https://bad-.example.test",
        "https://pohoda.example.test.",
        "https://1.2.3",
        "https://999.0.0.1",
        "https://exámple.test",
        "https://" + "a" * 64 + ".test",
        "https://" + ".".join(["abc"] * 80),
    ],
)
@pytest.mark.anyio
async def test_bad_mserver_urls_fail_closed_before_any_request(raw: str) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=INVOICES)

    with pytest.raises(ConnectorError) as caught:
        mserver_origin(raw)
    assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    service = PohodaService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await service.test_connection(context(credentials={"mserver_url": raw}))
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"ico": "abc"}},
        {"credentials": {"ico": "123"}},
        {"credentials": {"ico": "1234567890123456"}},
        {"credentials": {"username": "user:name"}},
        {"credentials": {"password": "bad\r\npassword"}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": "pohoda/other/install-1"},
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
        return httpx.Response(200, content=INVOICES)

    service = PohodaService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, text="private bad request"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, text="private"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, text="private"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, text="private"), ErrorCode.NOT_FOUND),
        (httpx.Response(405, text="private"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (
            httpx.Response(302, headers={"Location": "https://outside.invalid/"}),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (httpx.Response(200, content=b"<html>private error</html>"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, content=b"not xml private"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, content=b""), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"private": 1}), ErrorCode.UPSTREAM_ERROR),
        (
            httpx.Response(
                200, content=b'<!DOCTYPE x [<!ENTITY e "private">]>' + INVOICES.split(b"?>", 1)[1]
            ),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (
            httpx.Response(200, content=response_pack("listInvoice", "invoice", "", state="error")),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (
            httpx.Response(200, content=response_pack("listOrder", "order", "")),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (
            httpx.Response(
                200,
                content=response_pack(
                    "listInvoice", "invoice", "<lst:invoice>private</lst:invoice>"
                ),
            ),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (
            httpx.Response(
                200, content=b'<?xml version="1.0"?><mserver><message>private</message></mserver>'
            ),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (
            httpx.Response(
                200,
                content=b'<rsp:responsePack xmlns:rsp="'
                + RSP.encode()
                + b'" version="2.0" id="x" state="ok">'
                + b'<rsp:responsePackItem version="2.0" id="y" state="error" note="private"/>'
                + b"</rsp:responsePack>",
            ),
            ErrorCode.UPSTREAM_ERROR,
        ),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = PohodaService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["list_invoices"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


@pytest.mark.anyio
async def test_oversized_and_too_deep_responses_are_rejected() -> None:
    big = response_pack(
        "listInvoice",
        "invoice",
        "<lst:invoice><typ:note>" + "x" * (4 * 1024 * 1024) + "</typ:note></lst:invoice>",
    )
    service = PohodaService(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big))
    )
    spec = build_definition(service).tools["list_invoices"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR
    deep = (
        f'<rsp:responsePack xmlns:rsp="{RSP}" version="2.0" id="x" state="ok">'
        + "<a>" * 40
        + "</a>" * 40
        + "</rsp:responsePack>"
    )
    with pytest.raises(ConnectorError):
        parse_response(deep.encode())
    parsed = parse_response(INVOICES)
    assert parsed["@state"] == "ok" and parsed["@ico"] == ICO
    invoices = parsed["responsePackItem"]["listInvoice"]["invoice"]
    assert [row["invoiceHeader"]["id"] for row in invoices] == ["7", "8"]
    assert (
        invoices[0]["invoiceHeader"]["partnerIdentity"]["address"]["company"]
        == "Private Novák s.r.o."
    )


def test_filter_outside_windows_1250_uses_character_references() -> None:
    content = build_request(
        {"request": "listStockRequest", "ico": ICO, "filter": [("name", "Nov\u00e1k \u4e2d")]}
    )
    assert b"&#20013;" in content and b"Nov\xe1k" in content
    root = ET.fromstring(content)
    ftr = f"{{{NS['ftr']}}}"
    name = root[0][0].find(f"{{{NS['lStk']}}}requestStock/{ftr}filter/{ftr}name")
    assert name is not None and name.text == "Nov\u00e1k \u4e2d"


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_invoices", {"count": 0}),
        ("list_invoices", {"count": 501}),
        ("list_invoices", {"count": "5"}),
        ("list_invoices", {"count": True}),
        ("list_invoices", {"id_from": 0}),
        ("list_invoices", {"invoice_type": "invoice"}),
        ("list_invoices", {"date_from": "2024-13-01"}),
        ("list_invoices", {"date_from": "2024-02-30"}),
        ("list_invoices", {"date_from": "01.02.2024"}),
        ("list_invoices", {"date_from": "2024-03-01", "date_till": "2024-02-01"}),
        ("list_invoices", {"date_from": "2023-01-01", "date_till": "2024-01-03"}),
        ("list_invoices", {"ico": "12"}),
        ("list_invoices", {"ico": "1234567a"}),
        ("list_invoices", {"company": ""}),
        ("list_invoices", {"company": "x" * 256}),
        ("list_invoices", {"last_changes": "2024-01-01"}),
        ("list_invoices", {"last_changes": "2024-01-01T25:00:00"}),
        ("list_invoices", {"number": "FV1"}),
        ("list_invoices", {"query_filter": "1=1"}),
        ("list_invoices", {"mserver_url": "https://evil"}),
        ("list_invoices", {"name": "evil\r\nSTW-Authorization: other"}),
        ("get_invoice", {}),
        ("get_invoice", {"invoice_id": "7"}),
        ("get_invoice", {"invoice_id": 0}),
        ("get_invoice", {"invoice_id": 7, "invoice_type": "order"}),
        ("list_orders", {"order_type": "issuedInvoice"}),
        ("get_order", {"order_id": 2**53}),
        ("list_partners", {"name": "x" * 33}),
        ("list_partners", {"city": "x" * 46}),
        ("get_partner", {"partner_id": True}),
        ("list_stock", {"ean": "123"}),
        ("list_stock", {"ean": "859400123456a"}),
        ("list_stock", {"code": ""}),
        ("get_stock_item", {"stock_id": -1}),
        ("list_stock", []),
        ("list_stock", "text"),
        ("list_stock", None),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
