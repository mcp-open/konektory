"""Read-only POHODA mServer adapter.

mServer speaks POHODA XML over HTTP: ``POST /xml`` with ``STW-Authorization: Basic
base64(user:pass)`` and ``Content-Type: text/xml`` (Windows-1250). The origin is
customer-hosted and therefore comes from the ``mserver_url`` credential — the single
documented exception to fixed egress; it is validated to ``https://host[:port]`` with
no path, query, fragment or userinfo.

Only ``list*Request`` roots from ``READ_REQUESTS`` are ever serialised; any other
dataPackItem (``invoice`` import, ``deleteOrder``, ...) is refused before egress.
"""

from __future__ import annotations

import ipaddress
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    ToolEnvelope,
    UpstreamClient,
)
from openmcp_connector_runtime.provider import basic_auth, credentials, private_envelope
from pydantic import BaseModel

SLUG = "pohoda"
VERSION = "1.0.0"
XML_PATH = "/xml"
# Provenance never carries the customer host; the origin is a credential value.
SOURCE = "https://{mserver_url}/xml"
APPLICATION = "OpenMCP"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("mserver_url", "username", "password", "ico")
MAX_XML_BYTES = 4 * 1024 * 1024
SCHEMA = "http://www.stormware.cz/schema/version_2/"
NS: dict[str, str] = {
    "dat": SCHEMA + "data.xsd",
    "rsp": SCHEMA + "response.xsd",
    "lst": SCHEMA + "list.xsd",
    "lAdb": SCHEMA + "list_addBook.xsd",
    "lStk": SCHEMA + "list_stock.xsd",
    "ftr": SCHEMA + "filter.xsd",
    "typ": SCHEMA + "type.xsd",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_ICO = re.compile(r"[0-9]{6,15}")
_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


@dataclass(frozen=True)
class Read:
    prefix: str  # namespace prefix of the request element
    request: str  # ``requestInvoice`` etc.
    response: str  # ``listInvoice`` etc. in responsePackItem
    record: str  # ``invoice`` etc. inside the list element
    type_attr: str | None  # ``invoiceType`` / ``orderType``
    version_attr: str  # ``invoiceVersion`` / ``addressBookVersion`` / ...


# The only dataPackItem roots this adapter may send; everything else is refused.
READ_REQUESTS: dict[str, Read] = {
    "listInvoiceRequest": Read(
        "lst", "requestInvoice", "listInvoice", "invoice", "invoiceType", "invoiceVersion"
    ),
    "listOrderRequest": Read(
        "lst", "requestOrder", "listOrder", "order", "orderType", "orderVersion"
    ),
    "listAddressBookRequest": Read(
        "lAdb", "requestAddressBook", "listAddressBook", "addressbook", None, "addressBookVersion"
    ),
    "listStockRequest": Read("lStk", "requestStock", "listStock", "stock", None, "stockVersion"),
}


def mserver_origin(raw: str) -> str:
    """Accept only ``https://hostname-or-ipv4[:port]`` for the customer-hosted mServer."""
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        port = parsed.port
        labels = hostname.split(".")
        try:
            ipaddress.IPv4Address(hostname)
            named = True
        except ValueError:
            named = (
                len(hostname) <= 253
                and all(_LABEL.fullmatch(label) for label in labels)
                and not labels[-1].isdigit()
            )
        valid = (
            0 < len(raw) <= 256
            and raw == raw.strip()
            and raw.isascii()
            and not any(ord(char) < 32 or ord(char) == 127 for char in raw)
            and "\\" not in raw
            and "[" not in raw
            and parsed.scheme == "https"
            and bool(hostname)
            and named
            and parsed.netloc.lower() == hostname + (f":{port}" if port is not None else "")
            and (port is None or 1 <= port <= 65535)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path in ("", "/")
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Nepovolená adresa mServeru.")
    return f"https://{hostname}{'' if port is None else ':' + str(port)}"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def element_to_value(element: ET.Element, depth: int = 0, budget: list[int] | None = None) -> Any:
    """Attributes become ``@name`` keys, children a dict (repeated tags a list), leaves text."""
    budget = budget if budget is not None else [50_000]
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele je příliš složitá.")
    result: dict[str, Any] = {f"@{local(key)}": value for key, value in element.attrib.items()}
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if not result:
            return text
        if text:
            result["#text"] = text
        return result
    for child in children:
        name = local(child.tag)
        value = element_to_value(child, depth + 1, budget)
        if name in result:
            existing = result[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[name] = [existing, value]
        else:
            result[name] = value
    return result


def parse_response(raw: bytes) -> dict[str, Any]:
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatnou odpověď.")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError, LookupError) as exc:
        raise ConnectorError(
            ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatnou odpověď."
        ) from exc
    if root.tag != f"{{{NS['rsp']}}}responsePack":
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatnou odpověď.")
    value = element_to_value(root)
    return value if isinstance(value, dict) else {"#text": value}


def build_request(body: dict[str, Any]) -> bytes:
    """Serialise ``{"request", "ico", "type", "limit", "filter"}`` into a read-only dataPack."""
    name = body.get("request")
    if not isinstance(name, str) or name not in READ_REQUESTS:
        raise ConnectorError(ErrorCode.INTERNAL, "Nepovolený XML požadavek POHODA.")
    read = READ_REQUESTS[name]
    ico = body.get("ico")
    if not isinstance(ico, str) or not _ICO.fullmatch(ico):
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné IČ účetní jednotky.")

    def tag(prefix: str, local_name: str) -> str:
        return f"{{{NS[prefix]}}}{local_name}"

    root = ET.Element(
        tag("dat", "dataPack"),
        {
            "id": "openmcp",
            "ico": ico,
            "application": APPLICATION,
            "version": "2.0",
            "note": "OpenMCP read-only export",
        },
    )
    item = ET.SubElement(root, tag("dat", "dataPackItem"), {"id": "openmcp-1", "version": "2.0"})
    attributes = {"version": "2.0"}
    if read.type_attr is not None:
        attributes[read.type_attr] = str(body["type"])
    attributes[read.version_attr] = "2.0"
    request = ET.SubElement(item, tag(read.prefix, name), attributes)
    limit = body.get("limit") or {}
    if limit:
        element = ET.SubElement(request, tag(read.prefix, "limit"))
        if "idFrom" in limit:
            ET.SubElement(element, tag(read.prefix, "idFrom")).text = str(int(limit["idFrom"]))
        ET.SubElement(element, tag(read.prefix, "count")).text = str(int(limit["count"]))
    holder = ET.SubElement(request, tag(read.prefix, read.request))
    filters: list[Any] = body.get("filter") or []
    if filters:
        element = ET.SubElement(holder, tag("ftr", "filter"))
        for path, value in filters:
            parent = element
            for part in str(path).split("/"):
                parent = ET.SubElement(parent, tag("ftr", part))
            parent.text = str(value)
    # Characters outside Windows-1250 become numeric character references.
    return bytes(ET.tostring(root, encoding="windows-1250", xml_declaration=True))


class XmlTransport(httpx.AsyncBaseTransport):
    """Bridge between the SDK's JSON-only client and the XML-only mServer.

    The JSON body describes one allow-listed ``list*Request``; it is serialised as
    the documented dataPack, and the XML responsePack is converted to JSON so the
    shared client keeps its bounded reads, status mapping and complexity checks.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or request.url.path != XML_PATH:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená operace mServeru.")
        body = json.loads(request.content or b"{}")
        if not isinstance(body, dict):
            raise ConnectorError(ErrorCode.INTERNAL, "Neplatné tělo požadavku.")
        content = build_request(body)
        headers = request.headers.copy()
        headers["Content-Type"] = "text/xml; charset=windows-1250"
        headers["Content-Length"] = str(len(content))
        headers["Accept"] = "text/xml"
        response = await self.inner.handle_async_request(
            httpx.Request(request.method, request.url, headers=headers, content=content)
        )
        if response.status_code >= 300:
            return response
        chunks: list[bytes] = []
        received = 0
        try:
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > MAX_XML_BYTES:
                    raise ConnectorError(
                        ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele je příliš velká."
                    )
                chunks.append(chunk)
        finally:
            await response.aclose()
        payload = json.dumps(parse_response(b"".join(chunks)), ensure_ascii=True).encode()
        return httpx.Response(
            response.status_code, headers={"Content-Type": "application/json"}, content=payload
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class PohodaService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        origin = mserver_origin(values["mserver_url"])
        if not _ICO.fullmatch(values["ico"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné IČ účetní jednotky.")
        basic_auth(values["username"], values["password"])
        return values, origin

    async def xml(
        self,
        context: InvocationContext,
        request: str,
        *,
        record_type: str | None = None,
        limit: dict[str, int] | None = None,
        filters: list[tuple[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str], str]:
        read = READ_REQUESTS.get(request)
        if read is None or (record_type is None) != (read.type_attr is None):
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolený XML požadavek POHODA.")
        values, origin = self._credentials(context)
        client = UpstreamClient(
            origin, transport=XmlTransport(self.transport), max_response_bytes=MAX_XML_BYTES
        )
        try:
            payload, _ = await client.request_json(
                "POST",
                XML_PATH,
                json_body={
                    "request": request,
                    "ico": values["ico"],
                    "type": record_type,
                    "limit": limit or {},
                    "filter": filters or [],
                },
                headers={
                    "STW-Authorization": basic_auth(values["username"], values["password"]),
                    "STW-Application": APPLICATION,
                    "User-Agent": USER_AGENT,
                },
                idempotent=True,  # list requests only; safe to retry 429/5xx
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or payload.get("@state") != "ok":
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "POHODA požadavek odmítla.")
        item = payload.get("responsePackItem")
        if isinstance(item, list):
            item = item[0] if item else None
        if not isinstance(item, dict) or item.get("@state") != "ok":
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "POHODA požadavek odmítla.")
        listing = item.get(read.response)
        if not isinstance(listing, dict) or listing.get("@state") != "ok":
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "POHODA vrátila neplatnou odpověď.")
        records = listing.get(read.record)
        rows: list[Any] = (
            [] if records is None else records if isinstance(records, list) else [records]
        )
        if not all(isinstance(row, dict) for row in rows):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "POHODA vrátila neplatnou odpověď.")
        return rows, values, origin

    async def listing(
        self,
        context: InvocationContext,
        request: str,
        args: dict[str, Any],
        record_type: str | None,
    ) -> ToolEnvelope:
        limit = {"count": args["count"]}
        if "id_from" in args:
            limit["idFrom"] = args["id_from"]
        filters: list[tuple[str, str]] = []
        for key, path in FILTERS[request].items():
            if key in args:
                filters.append((path, str(args[key])))
        rows, values, origin = await self.xml(
            context, request, record_type=record_type, limit=limit, filters=filters
        )
        return private_envelope(
            {"items": rows, "count": len(rows), "truncated": len(rows) >= args["count"]},
            context,
            SLUG,
            f"{origin}#{values['ico']}",
            values["pii_key"],
            SOURCE,
        )

    async def detail(
        self, context: InvocationContext, request: str, record_id: int, record_type: str | None
    ) -> ToolEnvelope:
        rows, values, origin = await self.xml(
            context,
            request,
            record_type=record_type,
            limit={"count": 1},
            filters=[("id", str(int(record_id)))],
        )
        if not rows:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Záznam nebyl nalezen.")
        return private_envelope(
            {"data": rows[0]},
            context,
            SLUG,
            f"{origin}#{values['ico']}",
            values["pii_key"],
            SOURCE,
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.xml(context, "listAddressBookRequest", limit={"count": 1})
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "list_invoices":
            return await self.listing(context, "listInvoiceRequest", args, args["invoice_type"])
        if name == "list_orders":
            return await self.listing(context, "listOrderRequest", args, args["order_type"])
        if name == "list_partners":
            return await self.listing(context, "listAddressBookRequest", args, None)
        if name == "list_stock":
            return await self.listing(context, "listStockRequest", args, None)
        if name == "get_invoice":
            return await self.detail(
                context, "listInvoiceRequest", args["invoice_id"], args["invoice_type"]
            )
        if name == "get_order":
            return await self.detail(
                context, "listOrderRequest", args["order_id"], args["order_type"]
            )
        if name == "get_partner":
            return await self.detail(context, "listAddressBookRequest", args["partner_id"], None)
        if name == "get_stock_item":
            return await self.detail(context, "listStockRequest", args["stock_id"], None)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")


# Tool argument -> ``ftr:filter`` element path per request (filter.xsd: filterDocsType,
# filterAdbsType, filterStocksType).
FILTERS: dict[str, dict[str, str]] = {
    "listInvoiceRequest": {
        "date_from": "dateFrom",
        "date_till": "dateTill",
        "company": "selectedCompanys/company",
        "ico": "selectedIco/ico",
        "last_changes": "lastChanges",
    },
    "listOrderRequest": {
        "date_from": "dateFrom",
        "date_till": "dateTill",
        "company": "selectedCompanys/company",
        "ico": "selectedIco/ico",
        "last_changes": "lastChanges",
    },
    "listAddressBookRequest": {
        "company": "company",
        "name": "name",
        "city": "city",
        "ico": "ico",
        "last_changes": "lastChanges",
    },
    "listStockRequest": {
        "code": "code",
        "ean": "EAN",
        "name": "name",
        "last_changes": "lastChanges",
    },
}
