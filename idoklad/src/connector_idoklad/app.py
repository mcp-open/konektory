from __future__ import annotations

from collections.abc import Awaitable, Callable

from openmcp_connector_runtime import (
    ConnectorDefinition,
    InvocationContext,
    ToolEnvelope,
    ToolSpec,
    create_app,
)
from pydantic import BaseModel
from starlette.applications import Starlette

from . import schemas as s
from .service import SLUG, VERSION, IdokladService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_issued_invoices",
        s.IssuedInvoiceList,
        "Stránka vydaných faktur s filtrem (datum, partner, stav úhrady) a řazením.",
    ),
    (
        "get_issued_invoice",
        s.IssuedInvoiceID,
        "Detail vydané faktury včetně položek, cen a adres.",
    ),
    (
        "list_received_invoices",
        s.ReceivedInvoiceList,
        "Stránka přijatých faktur s filtrem a řazením.",
    ),
    ("list_contacts", s.ContactList, "Stránka kontaktů (adresáře) s filtrem a řazením."),
    ("get_contact", s.ContactID, "Detail kontaktu podle Id."),
    (
        "list_bank_statements",
        s.BankStatementList,
        "Stránka bankovních výpisů (pohybů) s filtrem účtu, data a stavu.",
    ),
    (
        "list_issued_payments",
        s.PaymentList,
        "Stránka úhrad vydaných dokladů s filtrem faktury, partnera a data.",
    ),
    ("get_current_agenda", s.Empty, "Detail aktuálně autorizované agendy (firmy)."),
)


def handler(
    name: str, service: IdokladService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: IdokladService | None = None,
) -> ConnectorDefinition:
    service = service or IdokladService()
    return ConnectorDefinition(
        slug=SLUG,
        version=VERSION,
        requires_secret=True,
        tools={
            name: ToolSpec(name, model, handler(name, service), description, read_only=True)
            for name, model, description in SPECS
        },
        test_connection=service.test_connection,
        close=service.close,
    )


def create_runtime_app(*, signing_key: str | None = None) -> Starlette:
    return create_app(build_definition(), signing_key=signing_key)
