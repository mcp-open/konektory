"""Read-only adapter for the Comgate REST API v2.0 (apidoc.comgate.cz/api/rest/)."""

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

SLUG = "comgate"
VERSION = "1.0.0"
# Single documented REST origin; the ``.json`` suffix selects the JSON representation.
ORIGIN = "https://payments.comgate.cz/v2.0"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("merchant", "secret")
_MERCHANT = re.compile(r"[A-Za-z0-9._-]{1,64}")


class ComgateService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _MERCHANT.fullmatch(values["merchant"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný identifikátor merchant.")
        basic_auth(values["merchant"], values["secret"])
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
                    "Authorization": basic_auth(values["merchant"], values["secret"]),
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        # Every documented JSON body is an object or an array; ``code`` other than 0
        # is the provider's own error envelope (1100–1500) and never reaches the model.
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and payload.get("code", 0) != 0
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Comgate vrátil neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, values["merchant"], values["pii_key"], ORIGIN + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/method.json")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "get_payment":
            return await self.get(context, f"/payment/transId/{segment(args['trans_id'])}.json")
        if name == "list_transfers":
            params = {"test": "true"} if args["test"] else None
            day = segment(args["date"])
            return await self.get(context, f"/transferList/date/{day}.json", params)
        if name == "get_transfer":
            params = {"test": "true"} if args["test"] else None
            transfer = segment(args["transfer_id"])
            return await self.get(context, f"/singleTransfer/transferId/{transfer}.json", params)
        if name == "list_methods":
            query: dict[str, Any] = {}
            if "lang" in args:
                query["lang"] = args["lang"]
            if "currency" in args:
                query["curr"] = args["currency"]
            if "country" in args:
                query["country"] = args["country"]
            return await self.get(context, "/method.json", query or None)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
