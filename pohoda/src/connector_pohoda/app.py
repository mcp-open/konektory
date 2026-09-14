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
from .service import SLUG, VERSION, PohodaService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_invoices",
        s.InvoiceList,
        "Seznam dokladů agendy faktur (listInvoiceRequest) s filtrem období, firmy a IČ.",
    ),
    ("get_invoice", s.InvoiceID, "Detail dokladu agendy faktur podle ID."),
    (
        "list_orders",
        s.OrderList,
        "Seznam objednávek (listOrderRequest) s filtrem období, firmy a IČ.",
    ),
    ("get_order", s.OrderID, "Detail objednávky podle ID."),
    (
        "list_partners",
        s.PartnerList,
        "Seznam záznamů adresáře (listAddressBookRequest) s filtrem firmy, jména, obce a IČ.",
    ),
    ("get_partner", s.PartnerID, "Detail záznamu adresáře podle ID."),
    ("list_stock", s.StockList, "Seznam zásob (listStockRequest) s filtrem kódu, EAN a názvu."),
    ("get_stock_item", s.StockID, "Detail zásoby podle ID."),
)


def handler(
    name: str, service: PohodaService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: PohodaService | None = None,
) -> ConnectorDefinition:
    service = service or PohodaService()
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
