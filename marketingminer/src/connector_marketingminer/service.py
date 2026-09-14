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
from openmcp_connector_runtime.provider import credentials, private_envelope
from pydantic import BaseModel

SLUG = "marketingminer"
VERSION = "1.0.0"
ORIGIN = "https://profilers-api.marketingminer.com"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("api_token",)
_TOKEN = re.compile(r"[A-Za-z0-9._~-]{16,512}")


class MarketingminerService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _TOKEN.fullmatch(values["api_token"]):
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID, "Neplatný API token Marketing Miner."
            )
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any]
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(ORIGIN, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            # The provider only authenticates via the documented ``api_token`` query
            # parameter; the SDK strips query strings from provenance and errors.
            payload, _ = await client.request_json(
                "GET",
                path,
                params={**params, "api_token": values["api_token"]},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        finally:
            await client.close()
        # JSend: only ``status: success`` with a ``data`` member is a valid answer.
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "success"
            or not isinstance(payload.get("data"), (dict, list))
        ):
            raise ConnectorError(
                ErrorCode.UPSTREAM_ERROR, "Marketing Miner vrátil neplatnou odpověď."
            )
        return private_envelope(
            payload["data"], context, SLUG, "profilers", values["pii_key"], ORIGIN
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        # Cheapest documented call (3 credits); the provider has no free auth probe.
        await self.get(
            context, "/keywords/search-volume-data", {"lang": "cs", "keyword": "openmcp"}
        )
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "keyword_search_volume":
            return await self.get(
                context,
                "/keywords/search-volume-data",
                {"lang": args["lang"], "keyword": args["keyword"]},
            )
        if name == "keyword_suggestions":
            params: dict[str, Any] = {"lang": args["lang"], "keyword": args["keyword"]}
            if "suggestions_type" in args:
                params["suggestions_type"] = args["suggestions_type"]
            if args["with_keyword_data"]:
                params["with_keyword_data"] = "true"
            return await self.get(context, "/keywords/suggestions", params)
        if name in ("website_stats", "website_stats_range"):
            params = {"lang": args["lang"], "target": args["target"], "type": args["target_type"]}
            for key in ("scheme", "period"):
                if key in args:
                    params[key] = args[key]
            path = "/websites/stats" if name == "website_stats" else "/websites/stats-range"
            return await self.get(context, path, params)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
