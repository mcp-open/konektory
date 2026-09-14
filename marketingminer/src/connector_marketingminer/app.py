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
from .service import SLUG, VERSION, MarketingminerService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "keyword_search_volume",
        s.KeywordSearchVolume,
        "Měsíční hledanost, CPC, sezónnost a obtížnost jednoho klíčového slova pro daný trh.",
    ),
    (
        "keyword_suggestions",
        s.KeywordSuggestions,
        "Návrhy souvisejících klíčových slov (otázky, nová, trendová) k zadanému výrazu.",
    ),
    (
        "website_stats",
        s.WebsiteStats,
        "Viditelnost domény nebo URL ve vyhledávání: odhad návštěvnosti a počet klíčových slov.",
    ),
    (
        "website_stats_range",
        s.WebsiteStatsRange,
        "Vývoj viditelnosti domény nebo URL v čase (denní, týdenní nebo měsíční řada).",
    ),
)


def handler(
    name: str, service: MarketingminerService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: MarketingminerService | None = None) -> ConnectorDefinition:
    service = service or MarketingminerService()
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
