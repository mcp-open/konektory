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
from .service import SLUG, VERSION, KrosService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_invoices",
        s.InvoiceList,
        "Seznam faktur s filtrem data vystavení/dodání, stavu úhrady a čísla dokladu.",
    ),
    ("get_invoice", s.InvoiceID, "Detail faktury podle Id včetně položek a cen."),
    (
        "list_proforma_invoices",
        s.ProformaInvoiceList,
        "Seznam zálohových (proforma) faktur s filtrem data, stavu úhrady a čísla dokladu.",
    ),
    (
        "list_expenses",
        s.ExpenseList,
        "Seznam výdajových dokladů (přijaté faktury, účtenky) s filtrem data a stavu úhrady.",
    ),
    ("get_expense", s.ExpenseID, "Detail výdajového dokladu podle Guid."),
    (
        "list_catalog_items",
        s.CatalogItemList,
        "Seznam položek katalogu (ceník, skladové množství) s filtrem kódu a názvu.",
    ),
    (
        "list_payments",
        s.PaymentList,
        "Seznam plateb (finančních transakcí) s filtrem data, účtu a externího id.",
    ),
    ("list_bank_accounts", s.Empty, "Seznam finančních účtů (banka, pokladna, platební brána)."),
)


def handler(
    name: str, service: KrosService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: KrosService | None = None,
) -> ConnectorDefinition:
    service = service or KrosService()
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
