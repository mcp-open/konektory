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

SLUG = "shoptet"
VERSION = "1.0.0"
ORIGIN = "https://api.myshoptet.com/api"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("access_token",)
# Documented add-on API access tokens are 38–60 characters; keep a bounded window.
_TOKEN = re.compile(r"[A-Za-z0-9._~-]{20,255}")
_TIME_FILTERS = {
    "creation_time_from": "creationTimeFrom",
    "creation_time_to": "creationTimeTo",
    "change_time_from": "changeTimeFrom",
    "change_time_to": "changeTimeTo",
}


class ShoptetService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _TOKEN.fullmatch(values["access_token"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný access token Shoptet.")
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
                    "Shoptet-Access-Token": values["access_token"],
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        # Documented envelope: ``data`` object plus an ``errors`` list (empty on success).
        if (
            not isinstance(payload, dict)
            or payload.get("errors")
            or not isinstance(payload.get("data"), dict)
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Shoptet vrátil neplatnou odpověď.")
        return private_envelope(payload["data"], context, SLUG, "eshop", values["pii_key"], ORIGIN)

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/eshop")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "list_orders":
            params: dict[str, Any] = {"page": args["page"], "itemsPerPage": args["items_per_page"]}
            if "status_id" in args:
                params["statusId"] = args["status_id"]
            params.update({wire: args[key] for key, wire in _TIME_FILTERS.items() if key in args})
            return await self.get(context, "/orders", params)
        if name == "get_order":
            return await self.get(context, f"/orders/{segment(args['code'])}")
        if name == "list_products":
            params = {"page": args["page"], "itemsPerPage": args["items_per_page"]}
            if "product_type" in args:
                params["type"] = args["product_type"]
            if "category_guid" in args:
                params["categoryGuid"] = args["category_guid"]
            if "change_time_from" in args:
                params["changeTimeFrom"] = args["change_time_from"]
            if args["include_images"]:
                params["include"] = "images"
            return await self.get(context, "/products", params)
        if name == "get_product":
            return await self.get(context, f"/products/{segment(args['guid'])}")
        if name == "get_product_by_code":
            return await self.get(context, f"/products/code/{segment(args['code'])}")
        if name == "list_customers":
            params = {"page": args["page"], "itemsPerPage": args["items_per_page"]}
            return await self.get(context, "/customers", params)
        if name == "get_customer":
            return await self.get(context, f"/customers/{segment(args['guid'])}")
        if name == "list_stocks":
            return await self.get(context, "/stocks")
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
