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
from .service import SLUG, VERSION, PacketaService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("packet_status", s.Packet, "Aktuální stav zásilky Packeta podle ID zásilky."),
    ("packet_tracking", s.Packet, "Celá historie stavů zásilky v síti Packeta."),
    ("packet_courier_tracking", s.Packet, "Historie stavů zásilky u externího dopravce."),
    ("packet_info", s.Packet, "Doplňující informace o zásilce a jejím předání dopravci."),
    ("packet_stored_until", s.Packet, "Datum, do kdy je zásilka uložena k vyzvednutí."),
    ("shipment_packets", s.Shipment, "Seznam zásilek ve svozovém listu podle D/B kódu."),
    ("list_carriers", s.CarrierList, "Stránkovaný seznam dopravců z feedu v5 s filtrem země."),
)


def handler(
    name: str, service: PacketaService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: PacketaService | None = None) -> ConnectorDefinition:
    service = service or PacketaService()
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
