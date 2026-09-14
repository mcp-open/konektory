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
from .service import SLUG, VERSION, MewsService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_reservations",
        s.ReservationList,
        "Stránka rezervací v ohraničeném časovém okně (max. 3 měsíce) s filtrem stavu a služby.",
    ),
    (
        "list_customers",
        s.CustomerList,
        "Stránka hostů podle intervalu vytvoření/změny nebo podle ID (adresy volitelně).",
    ),
    ("list_services", s.ServiceList, "Stránka služeb podniku (ubytování, doplňkové služby)."),
    ("list_resources", s.ResourceList, "Stránka zdrojů (pokojů, míst) podniku."),
    ("list_enterprises", s.EnterpriseList, "Stránka podniků dostupných přístupovému tokenu."),
    ("get_configuration", s.Configuration, "Konfigurace podniku svázaného s tokenem."),
)


def handler(
    name: str, service: MewsService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: MewsService | None = None,
) -> ConnectorDefinition:
    service = service or MewsService()
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
