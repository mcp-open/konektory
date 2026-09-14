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
from .service import SLUG, VERSION, ShoptetService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_orders", s.OrderList, "Stránka objednávek s filtrem stavu a data vytvoření či změny."),
    ("get_order", s.OrderCode, "Detail objednávky podle jejího kódu (čísla)."),
    (
        "list_products",
        s.ProductList,
        "Stránka produktů s filtrem typu, kategorie a data změny; volitelně s obrázky.",
    ),
    ("get_product", s.ProductGuid, "Detail produktu podle GUID."),
    ("get_product_by_code", s.ProductCode, "Detail produktu podle kódu varianty."),
    ("list_customers", s.CustomerList, "Stránka zákazníků e-shopu (pseudonymizováno)."),
    ("get_customer", s.CustomerGuid, "Detail zákazníka podle GUID (pseudonymizováno)."),
    ("list_stocks", s.Empty, "Seznam skladů e-shopu včetně výchozího skladu."),
)


def handler(
    name: str, service: ShoptetService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: ShoptetService | None = None) -> ConnectorDefinition:
    service = service or ShoptetService()
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
