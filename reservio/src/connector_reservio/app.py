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
from .service import SLUG, VERSION, ReservioService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("get_business", s.Input, "Detail podniku (adresa, nastavení rezervací, časová zóna)."),
    ("list_services", s.Input, "Seznam služeb podniku (délka, cena, kapacita)."),
    ("get_service", s.ServiceID, "Detail služby podniku."),
    ("list_resources", s.Input, "Seznam zdrojů podniku (zaměstnanci, místnosti)."),
    ("list_opening_hours", s.Input, "Otevírací doba podniku po dnech v týdnu."),
    (
        "booking_slots",
        s.BookingSlots,
        "Volné rezervační sloty pro službu (a volitelně zdroj) v ohraničeném intervalu.",
    ),
    ("list_events", s.Sorted, "Události kalendáře podniku seřazené podle vytvoření."),
    ("list_bookings", s.Sorted, "Rezervace podniku seřazené podle vytvoření."),
)


def handler(
    name: str, service: ReservioService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: ReservioService | None = None,
) -> ConnectorDefinition:
    service = service or ReservioService()
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
