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
from .service import SLUG, VERSION, HeurekaService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "get_order_status",
        s.OrderID,
        "Stav objednávky Heureka Marketplace podle ID objednávky (stav, interní a Heureka ID).",
    ),
    (
        "list_stores",
        s.Empty,
        "Pobočky a výdejní místa obchodu uložená na Heurece (ID, typ, název, město).",
    ),
    (
        "get_shop_status",
        s.Empty,
        "Stav aktivace obchodu v Heureka Marketplace včetně případné chyby deaktivace.",
    ),
)


def handler(
    name: str, service: HeurekaService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: HeurekaService | None = None,
) -> ConnectorDefinition:
    service = service or HeurekaService()
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
