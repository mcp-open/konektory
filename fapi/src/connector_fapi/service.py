"""Read-only adapter for FAPI (``https://api.fapi.cz``).

Authentication is the documented HTTP Basic scheme ``username:api_key``. Only the
documented GET list/detail endpoints are used; invoice/client/form creation,
updates, deletion, e-mail sending and PDF export are never callable.
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
from openmcp_connector_runtime.provider import basic_auth, credentials, private_envelope, segment
from pydantic import BaseModel

SLUG = "fapi"
VERSION = "1.0.0"
ORIGIN = "https://api.fapi.cz"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("username", "api_key")
_USERNAME = re.compile(r"[^\s:]{1,128}")
_API_KEY = re.compile(r"[A-Za-z0-9._~-]{16,512}")
# Date range arguments become the documented two-element array ``name[0]``/``name[1]``.
RANGES = ("create_date", "payday_date", "paid_on")
LISTS: dict[str, tuple[str, str]] = {
    "list_invoices": ("/invoices", "invoices"),
    "list_clients": ("/clients", "clients"),
    "list_forms": ("/forms", "forms"),
    "list_item_templates": ("/item_templates", "item_templates"),
    "list_payments": ("/payments", "payments"),
}
DETAILS: dict[str, tuple[str, str]] = {
    "get_invoice": ("/invoices", "invoice_id"),
    "get_client": ("/clients", "client_id"),
    "get_form": ("/forms", "form_id"),
}


def query(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name in RANGES:
        start, end = args.pop(f"{name}_from", None), args.pop(f"{name}_to", None)
        if start is not None and end is not None:
            params[f"{name}[0]"], params[f"{name}[1]"] = start, end
        elif start is not None or end is not None:
            raise ConnectorError(
                ErrorCode.INVALID_INPUT, "Rozsah data vyžaduje obě hranice (od i do)."
            )
    if "order_by" in args:
        params["order"] = args.pop("order_by")
    for key, value in args.items():
        if isinstance(value, bool):
            if value:
                params[key] = "true"
        else:
            params[key] = value
    return params


class FapiService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _USERNAME.fullmatch(values["username"]) or not _API_KEY.fullmatch(
            values["api_key"]
        ):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné credentials FAPI.")
        basic_auth(values["username"], values["api_key"])
        return values

    async def get(
        self,
        context: InvocationContext,
        path: str,
        params: dict[str, Any] | None = None,
        collection: str | None = None,
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(
            ORIGIN, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": basic_auth(values["username"], values["api_key"]),
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        # Documented error body is {"message": ..., "type": "...Exception"}; lists are
        # wrapped in their resource key ({"invoices": [...]}), details are plain objects.
        if (
            not isinstance(payload, dict)
            or ("message" in payload and "type" in payload and len(payload) == 2)
            or (collection is not None and not isinstance(payload.get(collection), list))
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "FAPI vrátilo neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, values["username"], values["pii_key"], ORIGIN + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/user")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name in LISTS:
            path, collection = LISTS[name]
            return await self.get(context, path, query(args), collection)
        if name in DETAILS:
            path, key = DETAILS[name]
            record = segment(args.pop(key))
            return await self.get(context, f"{path}/{record}", query(args) or None)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
