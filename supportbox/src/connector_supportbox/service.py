"""Read-only service for SupportBox API v2 (fixed origin, Bearer API key)."""

from __future__ import annotations

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

SLUG = "supportbox"
VERSION = "1.0.0"
ORIGIN = "https://app.supportbox.cz/api/rest/v2"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("api_token",)
LISTS = {"list_mailboxes": "/mailboxes", "list_users": "/users", "list_tags": "/tags"}


class SupportboxService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        token = values["api_token"]
        if not token.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in token):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API token.")
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(ORIGIN, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {values['api_token']}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("items"), list)
            or "error" in payload
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "SupportBox vrátil neplatnou odpověď.")
        # Keep the documented page metadata readable next to the complete item list.
        pagination = payload.get("pagination")
        meta = pagination if isinstance(pagination, dict) else {}
        result = {
            "items": payload["items"],
            "page": meta.get("page"),
            "limit": meta.get("per_page"),
            "total": meta.get("total"),
            "pages": meta.get("total_pages"),
        }
        return private_envelope(result, context, SLUG, ORIGIN, values["pii_key"], ORIGIN)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/users", {"page": 1, "per_page": 1})
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        page: dict[str, Any] = {key: args[key] for key in ("page", "per_page") if key in args}
        if name == "list_mail_tickets":
            params: dict[str, Any] = dict(page)
            for key, field in (
                ("status", "status"),
                ("mailbox_id", "mailbox_id"),
                ("assigned_user_id", "assigned_user_id"),
                ("tag_id", "tag_id"),
            ):
                if key in args:
                    params[f"filter[{field}][eq]"] = args[key]
            if args["unassigned"]:
                params["filter[assigned_user_id][eq]"] = "null"
            for key, field, operator in (
                ("created_from", "created_at", "gte"),
                ("created_to", "created_at", "lte"),
                ("last_message_from", "last_message_at", "gte"),
                ("last_message_to", "last_message_at", "lte"),
            ):
                if key in args:
                    params[f"filter[{field}][{operator}]"] = args[key]
            return await self.get(context, "/mail-tickets", params)
        if name == "get_mail_ticket":
            # The public API has no single-ticket endpoint; the documented way is the
            # listing filtered by exact id.
            return await self.get(
                context, "/mail-tickets", {"filter[id][eq]": args["ticket_id"], "per_page": 1}
            )
        if name == "list_mail_ticket_messages":
            return await self.get(
                context, f"/mail-tickets/{segment(args['ticket_id'])}/messages", page
            )
        if name in LISTS:
            return await self.get(context, LISTS[name], page)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
