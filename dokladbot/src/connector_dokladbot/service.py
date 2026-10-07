"""Read-only adapter over the DokladBot REST API (https://dokladbot.cz/api).

The installation supplies a personal DokladBot API key (``dkb_live_`` prefix,
created in the dashboard under Nastavení, API klíče). Every call is a GET to a
fixed path on the single DokladBot origin; nothing is created, sent or deleted.
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

SLUG = "dokladbot"
VERSION = "1.0.0"
ORIGIN = "https://dokladbot.cz/api"
USER_AGENT = "OpenMCP/1.0 (podpora@openmcp.cz)"
REQUIRED_CREDENTIALS = ("api_key",)
API_KEY = re.compile(r"dkb_live_[A-Za-z0-9_-]{32}")


class DokladbotService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not API_KEY.fullmatch(values["api_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API klíč DokladBotu.")
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values = self._credentials(context)
        clean = {key: value for key, value in (params or {}).items() if value is not None}
        client = UpstreamClient(ORIGIN, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=clean or None,
                headers={
                    "Authorization": f"Bearer {values['api_key']}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or "error" in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "DokladBot vrátil neplatnou odpověď.")
        return private_envelope(payload, context, SLUG, "dokladbot", values["pii_key"], ORIGIN)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/credits/balance")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump()
        if name == "list_documents":
            return await self.get(context, "/receipts", args)
        if name == "get_document":
            return await self.get(context, f"/receipts/{segment(args['document_id'])}")
        if name == "get_vat_summary":
            return await self.get(context, "/dph", args)
        if name == "list_due_invoices":
            return await self.get(context, "/due-dates", args)
        if name == "get_overview":
            return await self.get(context, "/dashboard")
        if name == "get_credit_balance":
            return await self.get(context, "/credits/balance")
        if name == "list_companies":
            return await self.get(context, "/companies")
        if name == "list_suppliers":
            return await self.get(context, "/suppliers", args)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
