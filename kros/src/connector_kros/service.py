"""Read-only adapter for KROS Fakturácia (KROS OpenAPI, ``api-economy.kros.sk``).

Authentication is the documented ``Authorization: Bearer <token>`` header with the
token generated in KROS Fakturácia settings (API prepojenia). Only documented GET
list/detail endpoints are used; the upload/batch POST endpoints are never callable.
"""

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

SLUG = "kros"
VERSION = "1.0.0"
ORIGIN = "https://api-economy.kros.sk/api"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("api_token",)
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,4096}")
# Documented enum values (Swagger: "0: NotPaid, 1: FullyPaid", "1: InDue, 2: OverDue").
PAYMENT_STATUS = {"not_paid": "0", "fully_paid": "1"}
DUE_DATE_STATUS = {"in_due": "1", "over_due": "2"}
# Argument name -> documented query parameter, shared by every list tool.
QUERY_NAMES = {
    "top": "Top",
    "skip": "Skip",
    "last_modified_from": "LastModifiedTimestamp",
    "issue_date_from": "IssueDateFrom",
    "issue_date_to": "IssueDateTo",
    "delivery_date_from": "DeliveryDateFrom",
    "delivery_date_to": "DeliveryDateTo",
    "due_date_from": "DueDateFrom",
    "due_date_to": "DueDateTo",
    "payment_date_from": "PaymentDateFrom",
    "payment_date_to": "PaymentDateTo",
    "numbering_sequence": "NumberingSequence",
    "document_number_from": "DocumentNumberFrom",
    "document_number_to": "DocumentNumberTo",
    "order_number": "OrderNumber",
    "item_code": "ItemCode",
    "name": "Name",
    "account_id": "AccountId",
    "external_id": "ExternalId",
}
LISTS = {
    "list_invoices": "/invoices",
    "list_proforma_invoices": "/proforma-invoices",
    "list_expenses": "/expenses",
    "list_catalog_items": "/catalog-items",
    "list_payments": "/payments",
    "list_bank_accounts": "/payments/accounts",
}


def query(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, value in args.items():
        if key in QUERY_NAMES:
            params[QUERY_NAMES[key]] = value
        elif key == "payment_status":
            params["PaymentStatus"] = PAYMENT_STATUS[value]
        elif key == "due_date_status":
            params["DueDateStatus"] = DUE_DATE_STATUS[value]
        elif key == "extended_fields":
            if value:
                params["ExtendedFields"] = ",".join(dict.fromkeys(value))
        elif key == "only_marked_for_eshop":
            if value:
                params["OnlyMarkedForEshop"] = "true"
        else:
            raise ConnectorError(ErrorCode.INTERNAL, "Neznámý argument.")
    return params


class KrosService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _TOKEN.fullmatch(values["api_token"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API token KROS.")
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
                    "Authorization": f"Bearer {values['api_token']}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        # Documented envelope: {"data": ...}; anything else (errors, HTML) fails closed.
        if not isinstance(payload, dict) or "data" not in payload or "errors" in payload:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "KROS vrátil neplatnou odpověď.")
        return private_envelope(
            payload["data"], context, SLUG, "kros-fakturacia", values["pii_key"], ORIGIN + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/numberingSequences")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name in LISTS:
            params = query(args)
            if name == "list_catalog_items" and "LastModifiedTimestamp" in params:
                # The catalog endpoint documents this filter under a different name.
                params["CatalogItemChangedTimestamp"] = params.pop("LastModifiedTimestamp")
            return await self.get(context, LISTS[name], params or None)
        if name == "get_invoice":
            return await self.get(context, f"/invoices/{segment(args['invoice_id'])}")
        if name == "get_expense":
            return await self.get(context, f"/expenses/{segment(args['expense_id'].lower())}")
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
