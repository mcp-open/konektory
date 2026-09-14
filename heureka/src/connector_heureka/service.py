"""Read-only adapter for the Heureka Marketplace API ("API Heureka" side).

The marketplace protocol has two halves: Heureka calls the shop's API (product
availability, delivery options, order push) and the shop calls Heureka. Only the
second half is reachable from here, and of it only the three documented JSON
reads (order status, branches, shop activation state). The API key is a path
segment of every call, so the provenance URL is rebuilt with a placeholder and
the real key never leaves the request.
"""

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

SLUG = "heureka"
VERSION = "1.0.0"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
# Only the two documented marketplace hosts exist; the credential ``country``
# selects one, a free URL is never accepted.
ORIGINS: dict[str, str] = {
    "cz": "https://ssl.heureka.cz",
    "sk": "https://ssl.heureka.sk",
}
API = "/api/cart"
API_VERSION = "1"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("api_key", "country")
# Documented read operations (relative to ``/api/cart/{api_key}/1``); nothing else is sent.
READ_OPERATIONS: dict[str, str] = {
    "get_order_status": "/order/status",
    "list_stores": "/stores",
    "get_shop_status": "/shop/status",
}
_API_KEY = re.compile(r"[A-Za-z0-9]{16,128}")


class HeurekaService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        origin = ORIGINS.get(values["country"])
        if origin is None:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neznámá země Heureka.")
        if not _API_KEY.fullmatch(values["api_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API klíč Heureka.")
        return values, origin

    async def get(
        self, context: InvocationContext, operation: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        if operation not in READ_OPERATIONS.values():
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená operace.")
        values, origin = self._credentials(context)
        client = UpstreamClient(
            origin + API, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                f"/{segment(values['api_key'])}/{API_VERSION}{operation}",
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Heureka vrátila neplatnou odpověď.")
        # The key is a URL segment; provenance carries a placeholder instead of the secret.
        source = f"{origin}{API}/{{api_key}}/{API_VERSION}{operation}"
        return private_envelope(payload, context, SLUG, origin, values["pii_key"], source)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, READ_OPERATIONS["get_shop_status"])
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        if name not in READ_OPERATIONS:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
        args = arguments.model_dump(exclude_none=True)
        params = {"order_id": args["order_id"]} if name == "get_order_status" else None
        return await self.get(context, READ_OPERATIONS[name], params)
