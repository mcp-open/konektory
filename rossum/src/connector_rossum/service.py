"""Read-only Rossum adapter over a validated per-organisation ``*.rossum.app`` origin."""

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

SLUG = "rossum"
VERSION = "1.0.0"
ORIGIN_SUFFIX = "rossum.app"
API_PATH = "/api/v1"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
# ``api_token`` is a long-lived token issued in Rossum; login with a password is never done here.
REQUIRED_CREDENTIALS = ("base_url", "api_token")


class RossumService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        origin = validated_origin(values["base_url"], suffix=ORIGIN_SUFFIX, path=API_PATH)
        token = values["api_token"]
        if len(token) > 512 or any(ord(char) < 33 or ord(char) == 127 for char in token):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný token Rossum.")
        return values, origin

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values, origin = self._credentials(context)
        client = UpstreamClient(
            origin, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
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
        if not isinstance(payload, dict) or "detail" in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Rossum vrátil neplatnou odpověď.")
        return private_envelope(payload, context, SLUG, origin, values["pii_key"], origin)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/auth/user")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "list_workspaces":
            return await self.get(context, "/workspaces", args)
        if name == "list_queues":
            params = {key: value for key, value in args.items() if key != "workspace_id"}
            if "workspace_id" in args:
                params["workspace"] = args["workspace_id"]
            return await self.get(context, "/queues", params)
        if name == "get_queue":
            return await self.get(context, f"/queues/{segment(args['queue_id'])}")
        if name == "list_annotations":
            params = {
                key: value for key, value in args.items() if key not in ("queue_id", "status")
            }
            if "queue_id" in args:
                params["queue"] = args["queue_id"]
            if "status" in args:
                params["status"] = ",".join(dict.fromkeys(args["status"]))
            return await self.get(context, "/annotations", params)
        if name == "get_annotation":
            return await self.get(context, f"/annotations/{segment(args['annotation_id'])}")
        if name == "get_annotation_content":
            annotation = segment(args["annotation_id"])
            return await self.get(context, f"/annotations/{annotation}/content")
        if name == "list_documents":
            return await self.get(context, "/documents", args)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
