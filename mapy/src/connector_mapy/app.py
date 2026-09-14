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
from .service import SLUG, VERSION, MapyService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("geocode", s.Query, "Geokódování: souřadnice a popis místa pro adresu, obec nebo název."),
    ("reverse_geocode", s.ReverseGeocode, "Zpětné geokódování: adresa a regiony pro souřadnice."),
    ("suggest", s.Query, "Našeptávač míst pro rozepsaný dotaz (nejvýše 15 návrhů)."),
    (
        "route",
        s.Route,
        "Plánování trasy mezi dvěma body (auto, pěšky, kolo) s délkou, časem a polyline.",
    ),
    ("elevation", s.Elevation, "Nadmořská výška pro zadané souřadnice (nejvýše 64 bodů)."),
)


def handler(
    name: str, service: MapyService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: MapyService | None = None) -> ConnectorDefinition:
    service = service or MapyService()
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
