"""Read-only Packeta (Zásilkovna) adapter.

Two documented read surfaces are used:

* REST/XML API ``https://www.zasilkovna.cz/api/rest`` — HTTP POST whose XML root
  element is the method name (``packetStatus``, ``packetTracking``, ...) with
  ``apiPassword`` as the first child. Only tracking/lookup methods are allowed;
  packet creation, labels, cancellation or ``packetCourierNumber`` (which consigns
  a packet to an external carrier) are never callable.
* Feed v5 ``https://pickup-point.api.packeta.com/v5/{apiKey}/carrier/json`` — GET
  JSON list of carriers, filtered and paginated locally.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    ToolEnvelope,
    UpstreamClient,
)
from openmcp_connector_runtime.provider import credentials, private_envelope
from pydantic import BaseModel

SLUG = "packeta"
VERSION = "1.0.0"
REST_ORIGIN = "https://www.zasilkovna.cz/api"
REST_PATH = "/rest"
FEED_ORIGIN = "https://pickup-point.api.packeta.com/v5"
FEED_SOURCE = "https://pickup-point.api.packeta.com/v5/{apiKey}/carrier/json"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("api_password", "api_key")
# The only REST methods this adapter may send; everything else is rejected before egress.
READ_METHODS = frozenset(
    {
        "packetStatus",
        "packetTracking",
        "packetCourierTracking",
        "packetInfo",
        "packetGetStoredUntil",
        "shipmentPackets",
    }
)
MAX_XML_BYTES = 1024 * 1024
_KEY = re.compile(r"[A-Za-z0-9]{16,64}")
_FAULT_CODES = {
    "IncorrectApiPasswordFault": (ErrorCode.CREDENTIAL_INVALID, "Packeta odmítla API heslo."),
    "PacketIdFault": (ErrorCode.NOT_FOUND, "Zásilka nebyla nalezena."),
    "ShipmentNotFoundFault": (ErrorCode.NOT_FOUND, "Svozový list nebyl nalezen."),
}


def element_to_value(element: ET.Element, depth: int = 0, budget: list[int] | None = None) -> Any:
    """Children become a dict (repeated tags a list); leaves become their text."""
    budget = budget if budget is not None else [20_000]
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Odpověď poskytovatele je příliš složitá.")
    children = list(element)
    if not children:
        return element.text or ""
    result: dict[str, Any] = {}
    for child in children:
        value = element_to_value(child, depth + 1, budget)
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result


def parse_xml(raw: bytes) -> Any:
    lowered = raw[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatnou odpověď.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ConnectorError(
            ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatnou odpověď."
        ) from exc
    if root.tag != "response":
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatnou odpověď.")
    return element_to_value(root)


class XmlTransport(httpx.AsyncBaseTransport):
    """Bridge between the SDK's JSON-only client and Packeta's XML-only REST endpoint.

    The JSON body ``{"<method>": {field: value}}`` is serialised as the documented XML
    request; a successful XML reply is converted to JSON so the shared client keeps
    doing bounded reads, status mapping and complexity checks.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if not isinstance(body, dict) or len(body) != 1:
            raise ConnectorError(ErrorCode.INTERNAL, "Neplatné tělo požadavku.")
        (method, fields), *_ = body.items()
        if method not in READ_METHODS or not isinstance(fields, dict):
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená metoda Packeta API.")
        root = ET.Element(method)
        for name, value in fields.items():
            ET.SubElement(root, str(name)).text = str(value)
        content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        headers = request.headers.copy()
        headers["Content-Type"] = "text/xml; charset=utf-8"
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
        payload = json.dumps(parse_xml(b"".join(chunks)), ensure_ascii=True).encode()
        return httpx.Response(
            response.status_code, headers={"Content-Type": "application/json"}, content=payload
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class PacketaService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _KEY.fullmatch(values["api_key"]) or not _KEY.fullmatch(values["api_password"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné Packeta API klíče.")
        return values

    async def rest(
        self, context: InvocationContext, method: str, fields: dict[str, Any]
    ) -> ToolEnvelope:
        if method not in READ_METHODS:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená metoda Packeta API.")
        values = self._credentials(context)
        client = UpstreamClient(
            REST_ORIGIN, transport=XmlTransport(self.transport), max_response_bytes=MAX_XML_BYTES
        )
        try:
            payload, source_url = await client.request_json(
                "POST",
                REST_PATH,
                json_body={method: {"apiPassword": values["api_password"], **fields}},
                headers={"User-Agent": USER_AGENT},
                idempotent=True,
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or "status" not in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Packeta vrátila neplatnou odpověď.")
        if payload["status"] != "ok":
            fault = payload.get("fault")
            code, message = _FAULT_CODES.get(
                fault if isinstance(fault, str) else "",
                (ErrorCode.UPSTREAM_ERROR, "Packeta požadavek odmítla."),
            )
            raise ConnectorError(code, message)
        return private_envelope(
            {"data": payload.get("result", "")},
            context,
            SLUG,
            values["api_key"],
            values["pii_key"],
            source_url,
        )

    async def carriers(self, context: InvocationContext, args: dict[str, Any]) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(
            FEED_ORIGIN, transport=self.transport, max_response_bytes=4 * 1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                f"/{quote(values['api_key'], safe='')}/carrier/json",
                params={"lang": args["lang"]},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        finally:
            await client.close()
        items = payload if isinstance(payload, list) else None
        if isinstance(payload, dict):
            items = next((v for v in payload.values() if isinstance(v, list)), None)
        if items is None:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Packeta vrátila neplatnou odpověď.")
        country = args.get("country", "").casefold()
        name = args.get("name", "").casefold()
        matched = [
            item
            for item in items
            if isinstance(item, dict)
            and (not country or str(item.get("country", "")).casefold() == country)
            and (not name or name in str(item.get("name", "")).casefold())
        ]
        offset, limit = args["offset"], args["limit"]
        return private_envelope(
            {"items": matched[offset : offset + limit], "total": len(matched), "offset": offset},
            context,
            SLUG,
            values["api_key"],
            values["pii_key"],
            FEED_SOURCE,
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        # The carrier feed validates the API key; the API password is validated by
        # the first packet lookup (Packeta has no dedicated auth check).
        await self.carriers(context, {"lang": "cs", "limit": 1, "offset": 0})
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        packet = {
            "packet_status": "packetStatus",
            "packet_tracking": "packetTracking",
            "packet_courier_tracking": "packetCourierTracking",
            "packet_info": "packetInfo",
            "packet_stored_until": "packetGetStoredUntil",
        }
        if name in packet:
            return await self.rest(context, packet[name], {"packetId": args["packet_id"]})
        if name == "shipment_packets":
            return await self.rest(context, "shipmentPackets", {"shipmentId": args["shipment_id"]})
        if name == "list_carriers":
            return await self.carriers(context, args)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
