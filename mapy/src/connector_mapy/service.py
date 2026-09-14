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

SLUG = "mapy"
VERSION = "1.0.0"
ORIGIN = "https://api.mapy.com"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("api_key",)
_KEY = re.compile(r"[A-Za-z0-9._~-]{16,512}")


def coordinate(position: dict[str, Any]) -> str:
    """Documented unexploded ``{lon},{lat}`` form; values are already bounded floats."""
    parts = []
    for axis in ("lon", "lat"):
        value = position[axis]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatná souřadnice.")
        text = f"{float(value):.7f}".rstrip("0").rstrip(".")
        parts.append("0" if text in ("", "-0", "-") else text)
    return ",".join(parts)


class MapyService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _KEY.fullmatch(values["api_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API klíč Mapy.com.")
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any]
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(ORIGIN, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "X-Mapy-Api-Key": values["api_key"],
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or "error" in payload or "detail" in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Mapy.com vrátilo neplatnou odpověď.")
        return private_envelope(payload, context, SLUG, "rest", values["pii_key"], ORIGIN)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/v1/geocode", {"query": "Praha", "lang": "cs", "limit": 1})
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name in ("geocode", "suggest"):
            params: dict[str, Any] = {
                "query": args["query"],
                "lang": args["lang"],
                "limit": args["limit"],
            }
            if "entity_type" in args:
                params["type"] = args["entity_type"]
            if "locality" in args:
                params["locality"] = args["locality"]
            return await self.get(context, f"/v1/{name}", params)
        if name == "reverse_geocode":
            lon, lat = coordinate(args).split(",")
            return await self.get(
                context, "/v1/rgeocode", {"lon": lon, "lat": lat, "lang": args["lang"]}
            )
        if name == "route":
            params = {
                "start": coordinate(args["start"]),
                "end": coordinate(args["end"]),
                "routeType": args["route_type"],
                "lang": args["lang"],
                # Polyline keeps long routes inside the bounded envelope.
                "format": "polyline",
                "avoidToll": "true" if args["avoid_toll"] else "false",
                "avoidHighways": "true" if args["avoid_highways"] else "false",
            }
            if args["waypoints"]:
                params["waypoints"] = [coordinate(point) for point in args["waypoints"]]
            return await self.get(context, "/v1/routing/route", params)
        if name == "elevation":
            positions = [coordinate(point) for point in args["positions"]]
            return await self.get(
                context, "/v1/elevation", {"positions": positions, "lang": args["lang"]}
            )
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
