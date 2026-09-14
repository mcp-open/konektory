"""Read-only service for the Collabim API (fixed origin, GET only)."""

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

SLUG = "collabim"
VERSION = "1.0.0"
# The documented base URL; every request is a GET below this origin.
ORIGIN = "https://api.oncollabim.com"
ACCEPT = "application/collabim+json"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("api_key",)
# Documented read endpoints only; the service never builds any other path.
PATHS: dict[str, str] = {
    "list_projects": "/projects",
    "get_project": "/projects/{id}",
    "list_keywords": "/keywords",
    "keyword_positions": "/keyword-positions",
    "aggregated_positions": "/aggregated-keywords-positions",
    "position_distribution": "/position-distribution",
    "market_share": "/market-share",
    "list_activities": "/activities",
}
_API_KEY = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")


def _flag(value: bool) -> int:
    return 1 if value else 0


class CollabimService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _API_KEY.fullmatch(values["api_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API klíč Collabim.")
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
                    # Documented: the raw user key in the Authorization header (no scheme).
                    "Authorization": values["api_key"],
                    "Accept": ACCEPT,
                    "User-Agent": USER_AGENT,
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or "data" not in payload or "errors" in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Collabim vrátil neplatnou odpověď.")
        data = payload["data"]
        if not isinstance(data, (dict, list)):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Collabim vrátil neplatnou odpověď.")
        body: dict[str, Any] = {"data": data}
        if isinstance(data, list):
            body["count"] = len(data)
        return private_envelope(body, context, SLUG, ORIGIN, values["pii_key"], ORIGIN + path)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, PATHS["list_projects"], {"page": 1, "itemsPerPage": 1})
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        if name not in PATHS:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
        args = arguments.model_dump(exclude_none=True)
        if name == "get_project":
            return await self.get(context, f"/projects/{segment(args['project_id'])}")
        params: dict[str, Any] = {}
        if "page" in args:
            params["page"] = args["page"]
            params["itemsPerPage"] = args["items_per_page"]
        if "project_id" in args:
            params["projectId"] = args["project_id"]
        if "date_from" in args:
            params["from"], params["to"] = args["date_from"], args["date_to"]
        if name == "list_projects":
            if "name_like" in args:
                params["nameLike"] = args["name_like"]
            if "active" in args:
                params["active"] = _flag(args["active"])
        elif name == "list_keywords":
            if "keyword_like" in args:
                params["keywordLike"] = args["keyword_like"]
            if "starred" in args:
                params["starred"] = _flag(args["starred"])
        elif name == "keyword_positions" and "project_keyword_ids" in args:
            params["projectKeywordIds"] = ",".join(str(i) for i in args["project_keyword_ids"])
        elif name == "market_share":
            params["searchEngineId"] = args["search_engine_id"]
        elif name == "list_activities":
            for key, provider_key in (
                ("added_on_from", "addedOnFrom"),
                ("added_on_to", "addedOnTo"),
                ("type_id", "typeId"),
                ("state_id", "stateId"),
            ):
                if key in args:
                    params[provider_key] = args[key]
        if "tags" in args:
            params["tags"] = args["tags"]
        if "tag_name" in args:
            params["tagName"] = args["tag_name"]
        if "get_x_days" in args:
            params["getXDays"] = args["get_x_days"]
        return await self.get(context, PATHS[name], params)
