"""Read-only adapter for SmartEmailing API v3 (HTTP Basic ``username:api_key``)."""

from __future__ import annotations

import re
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
from openmcp_connector_runtime.provider import basic_auth, credentials, private_envelope, segment
from pydantic import BaseModel

SLUG = "smartemailing"
VERSION = "1.0.0"
ORIGIN = "https://app.smartemailing.cz/api/v3"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("username", "api_key")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}")


def contact_segment(value: str) -> str:
    """``/contacts/{emailaddress}`` accepts a numeric ID or an e-mail address."""
    if value.isdigit():
        return segment(value)
    if not _EMAIL.fullmatch(value):
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatný identifikátor kontaktu.")
    return quote(value, safe="@")


class SmartemailingService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        basic_auth(values["username"], values["api_key"])
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
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
        # Every documented response carries ``status: "ok"``; anything else is an error envelope.
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ConnectorError(
                ErrorCode.UPSTREAM_ERROR, "SmartEmailing vrátil neplatnou odpověď."
            )
        return private_envelope(
            payload, context, SLUG, values["username"], values["pii_key"], ORIGIN + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/check-credentials")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args: dict[str, Any] = arguments.model_dump(exclude_none=True)
        params: dict[str, Any]
        if name == "list_contacts":
            return await self.get(context, "/contacts", args)
        if name == "get_contact":
            params = {key: args[key] for key in ("select", "expand") if key in args}
            path = f"/contacts/{contact_segment(args['contact'])}"
            return await self.get(context, path, params)
        if name == "list_contactlists":
            return await self.get(context, "/contactlists", args)
        if name == "get_contactlist":
            params = {key: args[key] for key in ("select",) if key in args}
            return await self.get(
                context, f"/contactlists/{segment(args['contactlist_id'])}", params
            )
        if name == "list_emails":
            return await self.get(context, "/emails", args)
        if name == "list_newsletters":
            params = {"limit": args["limit"], "offset": args["offset"]}
            if "newsletter_id" in args:
                params["filter[id][eq]"] = args["newsletter_id"]
            if "email_id" in args:
                params["filter[email_id][eq]"] = args["email_id"]
            return await self.get(context, "/newsletter", params)
        if name == "newsletter_stats":
            return await self.get(context, "/newsletter-stats-summary", args)
        if name == "list_customfields":
            return await self.get(context, "/customfields", args)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
