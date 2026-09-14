"""Read-only service for the Reservio API v2 (fixed origin, JSON:API, GET only)."""

from __future__ import annotations

import re
from typing import Any

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    ToolEnvelope,
    UpstreamClient,
)
from openmcp_connector_runtime.provider import credentials, private_envelope, segment
from pydantic import BaseModel

from . import schemas as s

SLUG = "reservio"
VERSION = "1.0.0"
ORIGIN = "https://api.reservio.com"
API_PREFIX = "/v2"
ACCEPT = "application/vnd.api+json"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("access_token", "business_id")
# Documented read endpoints below /businesses/{businessId}; nothing else is ever built.
BUSINESS_PATHS: dict[str, str] = {
    "get_business": "",
    "list_services": "/services",
    "get_service": "/services/{service_id}",
    "list_resources": "/resources",
    "list_opening_hours": "/opening-hours",
    "booking_slots": "/availability/booking-slots",
    "list_events": "/events",
    "list_bookings": "/bookings",
}
TEST_PATH = "/users/me"  # documented token check
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,4096}")
_GUID = re.compile(s.GUID_PATTERN)


class ReservioService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _TOKEN.fullmatch(values["access_token"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný access token Reservio.")
        if not _GUID.fullmatch(values["business_id"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné ID podniku Reservio.")
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(
            ORIGIN + API_PREFIX, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {values['access_token']}",
                    "Accept": ACCEPT,
                    "User-Agent": USER_AGENT,
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or "errors" in payload or "data" not in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Reservio vrátilo neplatnou odpověď.")
        data = payload["data"]
        if not isinstance(data, (dict, list)):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Reservio vrátilo neplatnou odpověď.")
        body: dict[str, Any] = {"data": data}
        if isinstance(data, list):
            body["count"] = len(data)
            meta = payload.get("meta")
            total = meta.get("total") if isinstance(meta, dict) else None
            if isinstance(total, int) and not isinstance(total, bool):
                body["total"] = total
            links = payload.get("links")
            body["truncated"] = isinstance(links, dict) and links.get("next") is not None
        return private_envelope(
            body, context, SLUG, ORIGIN, values["pii_key"], ORIGIN + API_PREFIX + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, TEST_PATH)
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        if name not in BUSINESS_PATHS:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
        values = self._credentials(context)
        args = arguments.model_dump(exclude_none=True)
        suffix = BUSINESS_PATHS[name]
        if name == "get_service":
            suffix = f"/services/{segment(args['service_id'])}"
        path = f"/businesses/{segment(values['business_id'])}{suffix}"
        params: dict[str, Any] = {}
        if "sort" in args:
            params["sort"] = args["sort"]
        if "date_from" in args:
            params["filter[from]"], params["filter[to]"] = s.interval(
                args["date_from"], args["date_to"]
            )
            if "service_id" in args:
                params["filter[serviceId]"] = args["service_id"]
            if "resource_id" in args:
                params["filter[resourceId]"] = args["resource_id"]
        return await self.get(context, path, params or None)
