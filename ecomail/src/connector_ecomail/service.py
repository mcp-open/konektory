"""Read-only adapter for Ecomail API v2 (``https://api2.ecomailapp.cz``, header ``key``)."""

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
from openmcp_connector_runtime.provider import credentials, private_envelope, segment
from pydantic import BaseModel

SLUG = "ecomail"
VERSION = "1.0.0"
ORIGIN = "https://api2.ecomailapp.cz"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("api_key",)
_API_KEY = re.compile(r"[!-~]{16,512}")  # printable ASCII, no whitespace/control chars
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}")


def email_segment(value: str) -> str:
    """Path segment for ``/subscribers/{email}``; only a plain e-mail address is accepted."""
    if not _EMAIL.fullmatch(value):
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatná e-mailová adresa.")
    return quote(value, safe="@")


class EcomailService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _API_KEY.fullmatch(values["api_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API klíč Ecomail.")
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(
            ORIGIN, transport=self.transport, max_response_bytes=4 * 1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    # The documented authentication header is literally named ``key``.
                    "key": values["api_key"],
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and ("errors" in payload or "error" in payload)
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Ecomail vrátil neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, ORIGIN, values["pii_key"], ORIGIN + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/lists")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args: dict[str, Any] = arguments.model_dump(exclude_none=True)
        params: dict[str, Any]
        if name == "list_lists":
            return await self.get(context, "/lists")
        if name == "get_list":
            return await self.get(context, f"/lists/{segment(args['list_id'])}")
        if name == "list_subscribers":
            params = {key: args[key] for key in ("page", "per_page", "status")}
            return await self.get(context, f"/lists/{segment(args['list_id'])}/subscribers", params)
        if name == "get_subscriber":
            email = email_segment(args["email"])
            if "list_id" in args:
                return await self.get(
                    context, f"/lists/{segment(args['list_id'])}/subscriber/{email}"
                )
            return await self.get(context, f"/subscribers/{email}")
        if name == "list_campaigns":
            params = {key: args[key] for key in ("per_page", "sort_by", "sort_dir")}
            filters = {
                "campaign_id": "id",
                "title": "title",
                "subject": "subject",
                "status": "status",
                "date_from": "date_from",
                "date_to": "date_to",
            }
            for key, provider_key in filters.items():
                if key in args:
                    params[f"filters[{provider_key}]"] = args[key]
            return await self.get(context, "/campaigns", params)
        if name == "campaign_stats":
            params = {key: args[key] for key in ("from_date", "to_date") if key in args}
            return await self.get(
                context, f"/campaigns/{segment(args['campaign_id'])}/stats", params
            )
        if name == "list_templates":
            return await self.get(context, "/templates")
        if name == "list_automations":
            return await self.get(context, "/pipelines")
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
