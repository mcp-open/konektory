"""Read-only Vyfakturuj.cz adapter (REST API v2, Basic auth e-mail:API klíč)."""

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
from openmcp_connector_runtime.provider import basic_auth, credentials, private_envelope, segment
from pydantic import BaseModel

SLUG = "vyfakturuj"
VERSION = "1.0.0"
ORIGIN = "https://api.vyfakturuj.cz/2.0"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("email", "api_key")
# Documented GET reads without arguments (Nastavení).
SETTINGS: dict[str, str] = {
    "list_payment_methods": "/settings/payment-method/",
    "list_number_series": "/settings/number-series/",
    "list_tags": "/settings/tags/",
}
_EMAIL = re.compile(r"[^\s@:/]{1,128}@[^\s@:/]{1,128}")
_API_KEY = re.compile(r"\S{8,512}")


class VyfakturujService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _EMAIL.fullmatch(values["email"]) or not _API_KEY.fullmatch(values["api_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné přihlášení Vyfakturuj.")
        basic_auth(values["email"], values["api_key"])
        return values

    async def get(
        self,
        context: InvocationContext,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        collection: bool,
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(
            ORIGIN, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, source_url = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": basic_auth(values["email"], values["api_key"]),
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Vyfakturuj požadavek odmítlo.")
        if collection:
            if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
                raise ConnectorError(
                    ErrorCode.UPSTREAM_ERROR, "Vyfakturuj vrátilo neplatnou odpověď."
                )
            limit = params.get("rows_limit") if params else None
            data: Any = {
                "items": payload,
                "count": len(payload),
                "truncated": limit is not None and len(payload) >= limit,
            }
        elif isinstance(payload, dict):
            data = payload
        else:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Vyfakturuj vrátilo neplatnou odpověď.")
        return private_envelope(data, context, SLUG, values["email"], values["pii_key"], source_url)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        # Documented no-op endpoint: ``curl --user email:key https://api.vyfakturuj.cz/2.0/test/``.
        await self.get(context, "/test/", collection=False)
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name in SETTINGS:
            return await self.get(context, SETTINGS[name], collection=True)
        if name == "get_invoice":
            return await self.get(
                context, f"/invoice/{segment(args['invoice_id'])}/", collection=False
            )
        if name == "get_contact":
            return await self.get(
                context, f"/contact/{segment(args['contact_id'])}/", collection=False
            )
        if name == "get_template":
            return await self.get(
                context, f"/template/{segment(args['template_id'])}/", collection=False
            )
        if name in {"list_invoices", "list_contacts"}:
            params = {
                {"variable_symbol": "VS", "ic": "IC", "dic": "DIC"}.get(key, key): value
                for key, value in args.items()
                if key not in {"sort_by", "sort_dir"}
            }
            if "sort_by" in args:
                params["sort"] = f"{args['sort_by']}~{args['sort_dir']}"
            path = "/invoice/" if name == "list_invoices" else "/contact/"
            return await self.get(context, path, params, collection=True)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
