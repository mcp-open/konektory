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

SLUG = "freelo"
VERSION = "1.0.0"
ORIGIN = "https://api.freelo.io/v1"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("email", "api_key")


class FreeloService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not re.fullmatch(r"[^\s@:]{1,128}@[^\s@:]{1,128}", values["email"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný e-mail Freelo účtu.")
        basic_auth(values["email"], values["api_key"])
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
                    "Authorization": basic_auth(values["email"], values["api_key"]),
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and "errors" in payload
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Freelo vrátilo neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, values["email"], values["pii_key"], ORIGIN
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/users/me")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "list_projects":
            return await self.get(context, "/projects", args)
        if name == "get_project":
            return await self.get(context, f"/project/{segment(args['project_id'])}")
        if name == "list_tasks":
            params: dict[str, Any] = {"p": args["page"] - 1}
            if "project_id" in args:
                params["projects_ids[]"] = args["project_id"]
            if "search" in args:
                params["search_query"] = args["search"]
            return await self.get(context, "/all-tasks", params)
        if name == "get_task":
            return await self.get(
                context,
                f"/task/{segment(args['task_id'])}",
                {"comments_order": "desc", "comments_limit": args["comments_limit"]},
            )
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
