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
    basic_auth,
    credentials,
    private_envelope,
    validated_origin,
)


class UpgatesService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        # Clients are short-lived, and never shared between tenant invocations.
        pass

    async def get(
        self,
        context: InvocationContext,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> ToolEnvelope:
        # Preserve complete pages; never silently drop records before a page advance.
        values = credentials(context, "upgates", ("api_url", "api_login", "api_key"))
        origin = validated_origin(values["api_url"], suffix="admin.upgates.com", path="/api/v2")
        authorization = basic_auth(values["api_login"], values["api_key"])
        client = UpstreamClient(
            origin,
            transport=self.transport,
            max_response_bytes=1024 * 1024,
            max_attempts=2,
        )
        try:
            payload, _ = await client.request_json(
                "GET", path, params=params, headers={"Authorization": authorization}
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatná data.")
        if isinstance(payload, dict) and "success" in payload:
            if payload["success"] is not True:
                raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel odmítl dotaz.")
            if "data" in payload and not isinstance(payload["data"], (dict, list)):
                raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatná data.")
        # Keep pagination alongside data; unwrapping would discard outer page metadata.
        return private_envelope(
            payload,
            context,
            "upgates",
            origin,
            values["pii_key"],
            "https://www.upgates.com",
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/status")
        return {"connected": True}
