"""Read-only adapter for iDoklad API v3.

Authentication is the documented OAuth2 *client credentials* flow: one form POST to
``https://identity.idoklad.cz/server/v2/connect/token`` (``grant_type``,
``application_id``, ``client_id``, ``client_secret``, ``scope=idoklad_api``) returns a
Bearer token that is used for the GET requests against ``https://api.idoklad.cz/v3``.
The token lives only inside the invocation that obtained it; nothing is cached.
The token path is the only POST this adapter can ever send; every other request is
a GET to a documented list/detail endpoint.
"""

from __future__ import annotations

import base64
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
from openmcp_connector_runtime.provider import credentials, private_envelope, segment
from pydantic import BaseModel

SLUG = "idoklad"
VERSION = "1.0.0"
API_ORIGIN = "https://api.idoklad.cz/v3"
IDENTITY_ORIGIN = "https://identity.idoklad.cz"
TOKEN_PATH = "/server/v2/connect/token"
TOKEN_SCOPE = "idoklad_api"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("client_id", "client_secret", "application_id")
# Documented list endpoints with their allowed filter and sort columns.
LISTS: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    "list_issued_invoices": (
        "/IssuedInvoices",
        frozenset(
            {
                "Id",
                "CurrencyId",
                "DateLastChange",
                "DateOfIssue",
                "DateOfPayment",
                "DateOfMaturity",
                "Exported",
                "Description",
                "NumericSequenceId",
                "DocumentNumber",
                "ConstantSymbolId",
                "PartnerId",
                "IsPaid",
                "PaymentStatus",
                "RecurringInvoiceId",
                "NickName",
                "DateOfTaxing",
                "TagIds",
            }
        ),
        frozenset({"Id", "DocumentNumber", "DateOfIssue"}),
    ),
    "list_received_invoices": (
        "/ReceivedInvoices",
        frozenset(
            {
                "Id",
                "CurrencyId",
                "DateLastChange",
                "DateOfIssue",
                "DateOfPayment",
                "DateOfMaturity",
                "Exported",
                "Description",
                "NumericSequenceId",
                "DocumentNumber",
                "SupplierId",
                "PaymentStatus",
                "DateOfReceiving",
                "DateOfTaxing",
                "NickName",
                "VariableSymbol",
                "TagIds",
            }
        ),
        frozenset({"Id", "DocumentNumber", "DateOfIssue", "DateOfReceiving"}),
    ),
    "list_contacts": (
        "/Contacts",
        frozenset(
            {
                "Id",
                "DateLastChange",
                "IdentificationNumber",
                "CompanyName",
                "Email",
                "VatIdentificationNumber",
                "VatIdentificationNumberSk",
            }
        ),
        frozenset({"Id", "CompanyName"}),
    ),
    "list_bank_statements": (
        "/BankStatements",
        frozenset(
            {
                "Id",
                "BankAccountId",
                "DocumentNumber",
                "NumericSequenceId",
                "PeriodDateFrom",
                "PeriodDateTo",
                "Status",
                "MovementType",
                "DateOfTransaction",
                "PartnerName",
                "TagIds",
            }
        ),
        frozenset({"Id", "DateOfTransaction"}),
    ),
    "list_issued_payments": (
        "/IssuedDocumentPayments",
        frozenset(
            {
                "Id",
                "InvoiceId",
                "PaymentOptionId",
                "DateOfPayment",
                "PartnerId",
                "InvoiceDocumentNumber",
                "Partner",
            }
        ),
        frozenset({"Id", "DateOfPayment"}),
    ),
}
DETAILS: dict[str, tuple[str, str]] = {
    "get_issued_invoice": ("/IssuedInvoices", "invoice_id"),
    "get_contact": ("/Contacts", "contact_id"),
}
_GUID = re.compile(r"[A-Za-z0-9._~:-]{8,256}")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,4096}")
# Values in this alphabet are sent verbatim (ids, id lists, ISO dates, enum names);
# anything else is base64-coded as the documentation recommends for text values.
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9.,:T-]{1,128}")


def filter_expression(clauses: list[dict[str, Any]], allowed: frozenset[str]) -> str:
    """Build the documented ``(Prop~op~value~and~Prop~op~value)`` expression."""
    parts: list[str] = []
    for clause in clauses:
        field, operator, value = clause["field"], clause["operator"], clause["value"]
        if field not in allowed:
            raise ConnectorError(ErrorCode.INVALID_INPUT, "Nepovolený sloupec filtru.")
        if _PLAIN_VALUE.fullmatch(value):
            parts.append(f"{field}~{operator}~{value}")
        elif operator in ("eq", "!eq", "ct", "!ct"):
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            parts.append(f"{field}~{operator}:base64~{encoded}")
        else:
            raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatná hodnota filtru.")
    return "(" + "~and~".join(parts) + ")"


class FormTransport(httpx.AsyncBaseTransport):
    """Re-encode the SDK's JSON body as the form fields the token endpoint requires.

    The shared ``UpstreamClient`` only serialises JSON bodies; the identity server
    only reads ``application/x-www-form-urlencoded``. Nothing else (URL, method,
    timeouts, bounded reading, status mapping) changes. The transport refuses any
    request that is not the documented token POST, so a bug elsewhere can never
    turn it into a write.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or request.url.path != TOKEN_PATH:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolený požadavek na identity server.")
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


class IdokladService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client and no token cache; every invocation owns its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not all(_GUID.fullmatch(values[key]) for key in REQUIRED_CREDENTIALS):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné credentials iDoklad.")
        return values

    async def _token(self, values: dict[str, str]) -> str:
        client = UpstreamClient(
            IDENTITY_ORIGIN,
            transport=FormTransport(self.transport),
            max_response_bytes=64 * 1024,
        )
        try:
            try:
                payload, _ = await client.request_json(
                    "POST",
                    TOKEN_PATH,
                    json_body={
                        "grant_type": "client_credentials",
                        "application_id": values["application_id"],
                        "client_id": values["client_id"],
                        "client_secret": values["client_secret"],
                        "scope": TOKEN_SCOPE,
                    },
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                )
            except ConnectorError as exc:
                if exc.provider_status == 400:
                    # Identity server answers 400 invalid_client for wrong id/secret.
                    raise ConnectorError(
                        ErrorCode.CREDENTIAL_INVALID, "iDoklad odmítl client credentials."
                    ) from exc
                raise
        finally:
            await client.close()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if (
            not isinstance(token, str)
            or not _TOKEN.fullmatch(token)
            or str(payload.get("token_type", "Bearer")).lower() != "bearer"
        ):
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID, "iDoklad nevydal přístupový token."
            )
        return token

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values = self._credentials(context)
        token = await self._token(values)
        client = UpstreamClient(
            API_ORIGIN, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        # Documented envelope: Data + Message/StatusCode/ErrorCode; ErrorCode 0 on success.
        if (
            not isinstance(payload, dict)
            or "Data" not in payload
            or payload.get("ErrorCode", 0) not in (0, None)
            or payload.get("Message")
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "iDoklad vrátil neplatnou odpověď.")
        return private_envelope(
            payload["Data"],
            context,
            SLUG,
            values["client_id"],
            values["pii_key"],
            API_ORIGIN + path,
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/Account/CurrentAgenda")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name in LISTS:
            path, filter_columns, sort_columns = LISTS[name]
            params: dict[str, Any] = {"page": args["page"], "pagesize": args["page_size"]}
            if args.get("filters"):
                params["filter"] = filter_expression(args["filters"], filter_columns)
                params["filtertype"] = args["filter_type"]
            if "sort_by" in args:
                if args["sort_by"] not in sort_columns:
                    raise ConnectorError(ErrorCode.INVALID_INPUT, "Nepovolený sloupec řazení.")
                params["sort"] = f"{args['sort_by']}~{args['sort_order']}"
            return await self.get(context, path, params)
        if name in DETAILS:
            path, key = DETAILS[name]
            return await self.get(context, f"{path}/{segment(args[key])}")
        if name == "get_current_agenda":
            return await self.get(context, "/Account/CurrentAgenda")
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
