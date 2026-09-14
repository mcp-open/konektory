"""Read-only Fio banka adapter.

Fio "API Bankovnictví" puts the account token into the URL path:

* ``/periods/{token}/{from}/{to}/transactions.json`` — movements in a period,
* ``/last/{token}/transactions.json`` — movements since the last download (the
  bank moves its server-side pointer after a non-empty answer),
* ``/by-id/{token}/{year}/{id}/transactions.json`` — an official statement,
* ``/lastStatement/{token}/statement`` — number of the latest statement (text).

Pointer setters (``set-last-id``, ``set-last-date``) and payment import are never
callable. The token is a credential: it is never logged, never written to the
provenance (``{token}`` placeholder) and never part of an error message.
"""

from __future__ import annotations

import json
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

from . import schemas as s

SLUG = "fio"
VERSION = "1.0.0"
ORIGIN = "https://fioapi.fio.cz/v1/rest"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("token",)
# Documented: the token is a 64-character unique string generated in internet banking.
_TOKEN = re.compile(r"[A-Za-z0-9]{64}")
_STATEMENT = re.compile(r"\s*(\d{4}),(\d{1,6})\s*")
# Documented column names of a movement -> stable keys; unknown names keep ``column{id}``.
COLUMNS: dict[str, str] = {
    "ID pohybu": "id",
    "Datum": "date",
    "Objem": "amount",
    "Měna": "currency",
    "Protiúčet": "counterAccount",
    "Název protiúčtu": "counterAccountName",
    "Kód banky": "bankCode",
    "Název banky": "bankName",
    "KS": "constantSymbol",
    "VS": "variableSymbol",
    "SS": "specificSymbol",
    "Uživatelská identifikace": "userIdentification",
    "Zpráva pro příjemce": "message",
    "Typ": "type",
    "Provedl": "executor",
    "Upřesnění": "specification",
    "Komentář": "comment",
    "BIC": "bic",
    "ID pokynu": "instructionId",
    "Reference plátce": "reference",
}
POINTER_WARNING = (
    "Fio po neprázdné odpovědi posunulo serverovou zarážku posledního stažení; "
    "další volání vrátí jen novější pohyby."
)


class FioTransport(httpx.AsyncBaseTransport):
    """Map documented Fio statuses and the text-only ``lastStatement`` reply for the SDK client.

    * 409 Conflict = the 30-second minimum interval per token was not kept (rate limit),
    * 500 = non-existent or inactive token (documented meaning),
    * ``/lastStatement/{token}/statement`` answers ``year,id`` as plain text; it is
      converted to JSON so the shared client keeps its bounded, validated reads.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if response.status_code == 409:
            await response.aclose()
            raise ConnectorError(
                ErrorCode.RATE_LIMITED,
                "Fio vyžaduje nejméně 30 s mezi dotazy se stejným tokenem.",
                retryable=True,
                provider_status=409,
            )
        if response.status_code == 500:
            await response.aclose()
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID,
                "Fio odmítla token (neexistuje nebo není aktivní).",
                provider_status=500,
            )
        if response.status_code >= 300 or not request.url.path.endswith("/statement"):
            return response
        chunks: list[bytes] = []
        received = 0
        try:
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > 64:
                    raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatnou odpověď.")
                chunks.append(chunk)
        finally:
            await response.aclose()
        match = _STATEMENT.fullmatch(b"".join(chunks).decode("ascii", errors="replace"))
        if match is None:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatnou odpověď.")
        payload = json.dumps({"year": int(match.group(1)), "id": int(match.group(2))}).encode()
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=payload)

    async def aclose(self) -> None:
        await self.inner.aclose()


def movement(row: Any) -> dict[str, Any]:
    """Flatten ``{"column22": {"value": ..., "name": "ID pohybu", "id": 22}}`` rows."""
    if not isinstance(row, dict):
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatný pohyb.")
    result: dict[str, Any] = {}
    for key, cell in row.items():
        if not re.fullmatch(r"column\d{1,3}", str(key)):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatný pohyb.")
        if cell is None:
            continue
        if not isinstance(cell, dict):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatný pohyb.")
        name = cell.get("name")
        result[COLUMNS.get(name, str(key)) if isinstance(name, str) else str(key)] = cell.get(
            "value"
        )
    return result


class FioService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _TOKEN.fullmatch(values["token"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný token Fio API.")
        return values

    async def fetch(self, context: InvocationContext, template: str) -> tuple[Any, str, str]:
        """GET ``template`` with ``{token}`` filled in; return payload, placeholder URL, pii key."""
        values = self._credentials(context)
        client = UpstreamClient(
            ORIGIN,
            transport=FioTransport(self.transport),
            max_attempts=1,  # documented 30 s per-token interval makes fast retries pointless
            max_response_bytes=4 * 1024 * 1024,
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                template.format(token=values["token"]),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        finally:
            await client.close()
        return payload, ORIGIN + template, values["pii_key"]

    def statement_envelope(
        self, context: InvocationContext, payload: Any, source: str, pii_key: str
    ) -> ToolEnvelope:
        if not isinstance(payload, dict) or not isinstance(payload.get("accountStatement"), dict):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatnou odpověď.")
        statement = payload["accountStatement"]
        info = statement.get("info")
        if not isinstance(info, dict):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatnou odpověď.")
        rows: Any = []
        transactions = statement.get("transactionList")
        if isinstance(transactions, dict):
            rows = transactions.get("transaction") or []
        if not isinstance(rows, list):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatnou odpověď.")
        movements = [movement(row) for row in rows]
        return private_envelope(
            {"info": info, "items": movements, "count": len(movements)},
            context,
            SLUG,
            self._credentials(context)["token"],
            pii_key,
            source,
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        # Read-only and pointer-neutral: the latest statement number only.
        payload, _, _ = await self.fetch(context, "/lastStatement/{token}/statement")
        if not isinstance(payload, dict) or "year" not in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatnou odpověď.")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "list_transactions":
            first, last = s.period(args["date_from"], args["date_to"])
            payload, source, pii_key = await self.fetch(
                context,
                f"/periods/{{token}}/{first.isoformat()}/{last.isoformat()}/transactions.json",
            )
            return self.statement_envelope(context, payload, source, pii_key)
        if name == "last_transactions":
            payload, source, pii_key = await self.fetch(context, "/last/{token}/transactions.json")
            result = self.statement_envelope(context, payload, source, pii_key)
            if result.data["count"]:
                result.warnings.append(POINTER_WARNING)
            return result
        if name == "get_statement":
            payload, source, pii_key = await self.fetch(
                context,
                f"/by-id/{{token}}/{int(args['year'])}/{int(args['statement_id'])}/transactions.json",
            )
            return self.statement_envelope(context, payload, source, pii_key)
        if name == "last_statement_number":
            payload, source, pii_key = await self.fetch(context, "/lastStatement/{token}/statement")
            if not isinstance(payload, dict):
                raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Fio vrátila neplatnou odpověď.")
            year, number = int(payload["year"]), int(payload["id"])
            result = private_envelope(
                {"year": year, "id": number},
                context,
                SLUG,
                self._credentials(context)["token"],
                pii_key,
                source,
            )
            # ``year`` is not a known safe key for the conservative pseudonymisation, so the
            # two plain integers (no personal data) are repeated in a warning like mews' cursor.
            result.warnings.append(f"Poslední oficiální výpis: rok {year}, číslo {number}.")
            return result
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
