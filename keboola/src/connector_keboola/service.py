"""Read-only Keboola Storage API adapter over a fixed allow-list of stacks."""

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

SLUG = "keboola"
VERSION = "1.0.0"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("stack", "storage_token")
# Public multi-tenant stacks per https://developers.keboola.com/overview/api/;
# the credential ``stack`` selects one, a free URL is never accepted.
ORIGINS = {
    "aws-us-east-1": "https://connection.keboola.com",
    "aws-eu-central-1": "https://connection.eu-central-1.keboola.com",
    "azure-north-europe": "https://connection.north-europe.azure.keboola.com",
    "gcp-us-east4": "https://connection.us-east4.gcp.keboola.com",
    "gcp-europe-west3": "https://connection.europe-west3.gcp.keboola.com",
}
API = "/v2/storage"


class KeboolaService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        origin = ORIGINS.get(values["stack"])
        if origin is None:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Nepodporovaný stack Keboola.")
        token = values["storage_token"]
        if len(token) > 512 or any(ord(char) < 33 or ord(char) == 127 for char in token):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný Storage token Keboola.")
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
                API + path,
                params=params,
                headers={
                    "X-StorageApi-Token": values["storage_token"],
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and ("error" in payload or "exceptionId" in payload)
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Keboola vrátila neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, values["stack"], values["pii_key"], origin + API
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/tokens/verify")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "verify_token":
            return await self.get(context, "/tokens/verify")
        if name == "list_buckets":
            include = {"include": "metadata"} if args["include_metadata"] else None
            return await self.get(context, "/buckets", include)
        if name == "list_tables":
            params = {"include": "columns,metadata" if args["include_columns"] else "metadata"}
            if "bucket_id" in args:
                bucket = segment(args["bucket_id"])
                return await self.get(context, f"/buckets/{bucket}/tables", params)
            return await self.get(context, "/tables", params)
        if name == "get_table":
            return await self.get(context, f"/tables/{segment(args['table_id'])}")
        if name == "preview_table":
            params = {"format": "json", "limit": args["limit"]}
            if "columns" in args:
                params["columns"] = ",".join(dict.fromkeys(args["columns"]))
            return await self.get(
                context, f"/tables/{segment(args['table_id'])}/data-preview", params
            )
        if name == "list_components":
            params = {}
            if "component_type" in args:
                params["componentType"] = args["component_type"]
            return await self.get(context, "/components", params)
        if name == "list_configurations":
            component = segment(args["component_id"])
            return await self.get(context, f"/components/{component}/configs")
        if name == "list_jobs":
            params = {"limit": args["limit"], "offset": args["offset"]}
            return await self.get(context, "/jobs", params)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
