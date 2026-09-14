"""Read-only service for Daktela V6 (per-tenant origin, static access token)."""

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
from openmcp_connector_runtime.provider import (
    credentials,
    private_envelope,
    segment,
    validated_origin,
)
from pydantic import BaseModel

SLUG = "daktela"
VERSION = "1.0.0"
# Only `https://<tenant>.daktela.com` is accepted; the API prefix is fixed here.
ORIGIN_SUFFIX = "daktela.com"
API_PREFIX = "/api/v6"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("instance_url", "access_token")


Condition = tuple[str, str, str]


def _filters(conditions: list[Condition]) -> dict[str, str]:
    # Daktela advanced filtering: filter[i][field|operator|value], joined with AND.
    params: dict[str, str] = {}
    for index, (field, operator, value) in enumerate(conditions):
        params[f"filter[{index}][field]"] = field
        params[f"filter[{index}][operator]"] = operator
        params[f"filter[{index}][value]"] = value
    return params


def _sort(field: str, direction: str) -> dict[str, str]:
    return {"sort[0][field]": field, "sort[0][dir]": direction}


class DaktelaService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        token = values["access_token"]
        if any(ord(char) < 33 or ord(char) == 127 for char in token) or not token.isascii():
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný přístupový token.")
        origin = validated_origin(values["instance_url"], suffix=ORIGIN_SUFFIX)
        return values, origin

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values, origin = self._credentials(context)
        client = UpstreamClient(
            origin + API_PREFIX, transport=self.transport, max_response_bytes=1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    # Recommended header authentication; the token never enters the URL.
                    "X-AUTH-TOKEN": values["access_token"],
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Daktela vrátila neplatnou odpověď.")
        # Unwrap the documented envelope (`error`, `result`, `_time`); list results keep
        # their `data` + `total` page metadata, details are the record itself.
        return private_envelope(
            payload["result"], context, SLUG, origin, values["pii_key"], origin + API_PREFIX
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/whoim.json")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        page = {key: args[key] for key in ("take", "skip") if key in args}
        conditions: list[Condition]
        if name == "list_tickets":
            conditions = [
                (field, "eq", str(args[field]))
                for field in ("stage", "priority", "category", "user", "contact")
                if field in args
            ]
            if "title_contains" in args:
                conditions.append(("title", "contains", str(args["title_contains"])))
            if "created_from" in args:
                conditions.append(("created", "gte", f"{args['created_from']} 00:00:00"))
            if "created_to" in args:
                conditions.append(("created", "lte", f"{args['created_to']} 23:59:59"))
            return await self.get(
                context,
                "/tickets.json",
                page | _filters(conditions) | _sort("created", args["sort"]),
            )
        if name == "get_ticket":
            return await self.get(context, f"/tickets/{segment(args['name'])}.json")
        if name == "list_activities":
            conditions = [
                (field, "eq", str(args[field]))
                for field in ("ticket", "type", "action", "queue", "user")
                if field in args
            ]
            return await self.get(
                context,
                "/activities.json",
                page | _filters(conditions) | _sort("time", args["sort"]),
            )
        if name == "list_contacts":
            conditions = []
            if "lastname_contains" in args:
                conditions.append(("lastname", "contains", str(args["lastname_contains"])))
            if "account" in args:
                conditions.append(("account", "eq", str(args["account"])))
            return await self.get(
                context, "/contacts.json", page | _filters(conditions) | _sort("lastname", "asc")
            )
        if name == "get_contact":
            return await self.get(context, f"/contacts/{segment(args['name'])}.json")
        if name == "list_queues":
            conditions = [("type", "eq", str(args["type"]))] if "type" in args else []
            return await self.get(
                context, "/queues.json", page | _filters(conditions) | _sort("title", "asc")
            )
        if name == "list_users":
            return await self.get(context, "/users.json", page | _sort("title", "asc"))
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
