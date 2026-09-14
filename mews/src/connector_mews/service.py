"""Read-only service for Mews Connector API (fixed environment allow-list, POST reads)."""

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
from openmcp_connector_runtime.provider import credentials, private_envelope
from pydantic import BaseModel

from . import schemas as s

SLUG = "mews"
VERSION = "1.0.0"
# Only these two documented platform addresses exist; never a free URL.
ORIGINS: dict[str, str] = {
    "production": "https://api.mews.com",
    "demo": "https://api.mews-demo.com",
}
API_PREFIX = "/api/connector/v1"
CLIENT = "OpenMCP 1.0.0"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("client_token", "access_token", "environment")
# The Connector API accepts POST only; these exact read operations are the whole allow-list.
READ_OPERATIONS: dict[str, tuple[str, str | None]] = {
    "list_reservations": ("/reservations/getAll/2023-06-06", "Reservations"),
    "list_customers": ("/customers/getAll", "Customers"),
    "list_services": ("/services/getAll", "Services"),
    "list_resources": ("/resources/getAll", "Resources"),
    "list_enterprises": ("/enterprises/getAll", "Enterprises"),
    "get_configuration": ("/configuration/get", None),
}
ALLOWED_PATHS = frozenset(path for path, _ in READ_OPERATIONS.values())
_TOKEN = re.compile(r"[A-Za-z0-9-]{16,256}")
_GUID = re.compile(s.GUID_PATTERN)


class MewsService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if values["environment"] not in ORIGINS:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neznámé prostředí Mews.")
        if not all(_TOKEN.fullmatch(values[key]) for key in ("client_token", "access_token")):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný token Mews.")
        return values, ORIGINS[values["environment"]]

    async def post(
        self,
        context: InvocationContext,
        path: str,
        body: dict[str, Any],
        collection: str | None,
    ) -> ToolEnvelope:
        if path not in ALLOWED_PATHS:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená operace.")
        values, origin = self._credentials(context)
        client = UpstreamClient(
            origin + API_PREFIX, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "POST",
                path,
                json_body={
                    "ClientToken": values["client_token"],
                    "AccessToken": values["access_token"],
                    "Client": CLIENT,
                    **body,
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                idempotent=True,  # documented read operations; safe to retry 429/5xx
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or "Message" in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Mews vrátilo neplatnou odpověď.")
        source = origin + API_PREFIX + path
        if collection is None:
            return private_envelope(payload, context, SLUG, origin, values["pii_key"], source)
        items = payload.get(collection)
        if not isinstance(items, list):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Mews vrátilo neplatnou odpověď.")
        cursor = payload.get("Cursor")
        result = private_envelope(
            {"data": items, "count": len(items), "truncated": cursor is not None},
            context,
            SLUG,
            origin,
            values["pii_key"],
            source,
        )
        if isinstance(cursor, str) and _GUID.fullmatch(cursor):
            # The cursor is the opaque provider identifier of the oldest returned datum;
            # it is the only way to fetch the next page and carries no personal data.
            result.warnings.append(f"Další stránka: cursor={cursor.lower()}")
        return result

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        path, collection = READ_OPERATIONS["get_configuration"]
        await self.post(context, path, {}, collection)
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        if name not in READ_OPERATIONS:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
        args = arguments.model_dump(exclude_none=True)
        path, collection = READ_OPERATIONS[name]
        body: dict[str, Any] = {}
        if "count" in args:
            body["Limitation"] = {"Count": args["count"]}
            if "cursor" in args:
                body["Limitation"]["Cursor"] = args["cursor"]
        if name == "list_reservations":
            key = {
                "scheduled_start": "ScheduledStartUtc",
                "scheduled_end": "ScheduledEndUtc",
                "colliding": "CollidingUtc",
                "created": "CreatedUtc",
                "updated": "UpdatedUtc",
            }[args["window"]]
            body[key] = s.interval(args["start"], args["end"])
            if "states" in args:
                body["States"] = args["states"]
            if "service_ids" in args:
                body["ServiceIds"] = args["service_ids"]
        elif name == "list_customers":
            body["Extent"] = {"Customers": True, "Addresses": args["include_addresses"]}
            if "start" in args:
                key = "CreatedUtc" if args["window"] == "created" else "UpdatedUtc"
                body[key] = s.interval(args["start"], args["end"])
            if "customer_ids" in args:
                body["CustomerIds"] = args["customer_ids"]
        elif name == "list_services":
            if "service_ids" in args:
                body["ServiceIds"] = args["service_ids"]
            if "service_type" in args:
                body["ServiceType"] = args["service_type"]
        elif name == "list_resources":
            body["Extent"] = {"Resources": True, "Inactive": args["include_inactive"]}
            if "resource_ids" in args:
                body["ResourceIds"] = args["resource_ids"]
            if "names" in args:
                body["Names"] = args["names"]
        elif name == "list_enterprises":
            if "enterprise_ids" in args:
                body["EnterpriseIds"] = args["enterprise_ids"]
        elif "enterprise_id" in args:
            body["EnterpriseId"] = args["enterprise_id"]
        return await self.post(context, path, body, collection)
