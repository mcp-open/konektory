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

SLUG = "fakturoid"
VERSION = "1.0.0"
ORIGIN = "https://app.fakturoid.cz/api/v3"
USER_AGENT = "OpenMCP/1.0 (podpora@openmcp.cz)"
REQUIRED_CREDENTIALS = ("account_slug", "access_token")


class FakturoidService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", values["account_slug"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný účet Fakturoidu.")
        if not re.fullmatch(r"[A-Za-z0-9._~-]{20,4096}", values["access_token"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný access token Fakturoidu.")
        return values

    async def get(
        self,
        context: InvocationContext,
        resource: str,
        record_id: int | None = None,
        page: int | None = None,
    ) -> ToolEnvelope:
        values = self._credentials(context)
        slug = values["account_slug"]
        path = f"/accounts/{slug}/{resource}"
        if record_id is not None:
            path += f"/{segment(record_id)}"
        path += ".json"
        params: dict[str, Any] | None = {"page": page} if page is not None else None
        client = UpstreamClient(ORIGIN, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {values['access_token']}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and ("error" in payload or "errors" in payload)
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fakturoid vrátil neplatnou odpověď.")
        if page is not None and isinstance(payload, list) and len(payload) > 40:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fakturoid překročil velikost stránky.")
        return private_envelope(payload, context, SLUG, slug, values["pii_key"], ORIGIN)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "invoices", page=1)
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump()
        routes: dict[str, tuple[str, str | None]] = {
            "list_invoices": ("invoices", None),
            "get_invoice": ("invoices", "invoice_id"),
            "list_subjects": ("subjects", None),
            "get_subject": ("subjects", "subject_id"),
            "list_expenses": ("expenses", None),
            "get_expense": ("expenses", "expense_id"),
        }
        route = routes.get(name)
        if route is None:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
        resource, id_field = route
        if id_field is None:
            return await self.get(context, resource, page=args["page"])
        return await self.get(context, resource, record_id=args[id_field])
