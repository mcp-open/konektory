from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

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

SLUG = "superfaktura"
VERSION = "1.0.0"
REQUIRED_CREDENTIALS = ("region", "email", "api_key")
ORIGINS = {
    "sk": "https://moja.superfaktura.sk",
    "cz": "https://moje.superfaktura.cz",
    "sandbox-sk": "https://sandbox.superfaktura.sk",
    "sandbox-cz": "https://sandbox.superfaktura.cz",
}


class SuperfakturaService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        origin = ORIGINS.get(values["region"])
        if origin is None:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Nepodporovaný region SuperFaktúry.")
        email = values["email"]
        api_key = values["api_key"]
        if (
            not re.fullmatch(r"[^\s@]{1,128}@[^\s@]{1,128}", email)
            or len(api_key) > 256
            or any(ord(char) < 33 or ord(char) == 127 for char in api_key)
        ):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné credentials SuperFaktúry.")
        return values, origin

    async def get(self, context: InvocationContext, path: str) -> ToolEnvelope:
        values, origin = self._credentials(context)
        authorization = "SFAPI " + "&".join(
            (
                f"email={quote(values['email'], safe='')}",
                f"apikey={quote(values['api_key'], safe='')}",
                "module=OpenMCP%201.0",
            )
        )
        client = UpstreamClient(origin, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            payload, _ = await client.request_json(
                "GET", path, headers={"Authorization": authorization, "Accept": "application/json"}
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and ("error" in payload or "error_message" in payload)
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "SuperFaktúra vrátila neplatnú odpoveď.")
        return private_envelope(
            payload, context, SLUG, values["email"], values["pii_key"], origin
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/clients/index.json/listinfo:1/per_page:1/page:1")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args: dict[str, Any] = arguments.model_dump()
        if name == "list_invoices":
            path = (
                "/invoices/index.json/listinfo:1/"
                f"per_page:{args['per_page']}/page:{args['page']}"
            )
        elif name == "get_invoice":
            path = f"/invoices/view/{segment(args['invoice_id'])}.json"
        elif name == "list_clients":
            path = f"/clients/index.json/listinfo:1/per_page:{args['per_page']}/page:{args['page']}"
        elif name == "get_client":
            path = f"/clients/view/{segment(args['client_id'])}.json"
        elif name == "list_expenses":
            path = (
                "/expenses/index.json/listinfo:1/"
                f"per_page:{args['per_page']}/page:{args['page']}"
            )
        elif name == "get_expense":
            path = f"/expenses/view/{segment(args['expense_id'])}.json"
        else:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
        return await self.get(context, path)
