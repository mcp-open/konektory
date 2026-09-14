"""Read-only FinStat adapter (Slovak company register and financial data).

FinStat publishes its API only through the official client libraries
(github.com/finstat/ClientApi.PHP, github.com/finstat/ClientApi.CSharp): every
call is an HTTP POST with ``application/x-www-form-urlencoded`` fields ``apiKey``,
``Hash``, ``StationId``, ``StationName`` plus the method argument, and the ``.json``
suffix selects JSON output. ``Hash`` is
``sha256("SomeSalt+{apiKey}+{privateKey}++{parameter}+ended")`` where ``parameter``
is the IČO (or the autocomplete query, or ``ico|year`` for statement detail).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlencode

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

SLUG = "finstat"
VERSION = "1.0.0"
ORIGIN = "https://www.finstat.sk/api"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("api_key", "private_key")
STATION_ID = "openmcp"
STATION_NAME = "OpenMCP"
# Only these documented read endpoints may ever be requested (always POST + ".json").
ENDPOINTS = frozenset(
    {
        "/basic.json",
        "/detail.json",
        "/extended.json",
        "/ultimate.json",
        "/autocomplete.json",
        "/GetStatements.json",
        "/GetStatementDetail.json",
    }
)
_KEY = re.compile(r"[A-Za-z0-9._:-]{8,256}")


def verification_hash(api_key: str, private_key: str, parameter: str) -> str:
    """Exact formula from the official FinStat clients (``ComputeVerificationHash``)."""
    data = f"SomeSalt+{api_key}+{private_key}++{parameter}+ended"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class FormTransport(httpx.AsyncBaseTransport):
    """Re-encode the SDK's JSON body as the form fields FinStat requires.

    The shared ``UpstreamClient`` only serialises JSON bodies; FinStat only reads
    ``application/x-www-form-urlencoded`` POST fields. Nothing else (URL, method,
    timeouts, bounded reading, status mapping) changes.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        fields = json.loads(request.content or b"{}")
        if not isinstance(fields, dict):
            raise ConnectorError(ErrorCode.INTERNAL, "Neplatné tělo požadavku.")
        content = urlencode({str(k): str(v) for k, v in fields.items()}).encode("ascii")
        headers = request.headers.copy()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(content))
        return await self.inner.handle_async_request(
            httpx.Request(request.method, request.url, headers=headers, content=content)
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class FinstatService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _KEY.fullmatch(values["api_key"]) or not _KEY.fullmatch(values["private_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný FinStat API klíč.")
        return values

    async def post(
        self, context: InvocationContext, path: str, parameter: str, fields: dict[str, Any]
    ) -> ToolEnvelope:
        if path not in ENDPOINTS:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolený FinStat endpoint.")
        values = self._credentials(context)
        body = {
            "apiKey": values["api_key"],
            "Hash": verification_hash(values["api_key"], values["private_key"], parameter),
            "StationId": STATION_ID,
            "StationName": STATION_NAME,
            **fields,
        }
        client = UpstreamClient(
            ORIGIN, transport=FormTransport(self.transport), max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, source_url = await client.request_json(
                "POST",
                path,
                json_body=body,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                idempotent=True,
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "FinStat vrátil neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, values["api_key"], values["pii_key"], source_url
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        # Autocomplete is the cheapest authenticated call; it still validates key + hash.
        await self.post(context, "/autocomplete.json", "finstat", {"query": "finstat"})
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        detail = {
            "get_basic": "/basic.json",
            "get_detail": "/detail.json",
            "get_extended": "/extended.json",
            "get_ultimate": "/ultimate.json",
            "list_statements": "/GetStatements.json",
        }
        if name in detail:
            return await self.post(context, detail[name], args["ico"], {"ico": args["ico"]})
        if name == "autocomplete":
            return await self.post(
                context, "/autocomplete.json", args["query"], {"query": args["query"]}
            )
        if name == "get_statement":
            return await self.post(
                context,
                "/GetStatementDetail.json",
                f"{args['ico']}|{args['year']}",
                {"ico": args["ico"], "year": args["year"], "template": args["template"]},
            )
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
