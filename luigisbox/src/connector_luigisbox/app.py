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
from .service import SLUG, VERSION, LuigisboxService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("search", s.Search, "Fulltextové vyhledávání v katalogu s filtry, řazením a facetami."),
    ("autocomplete", s.Autocomplete, "Našeptávač: návrhy dotazů a položek pro částečný dotaz."),
    ("top_items", s.TopItems, "Nejoblíbenější položky podle typu (např. pro prázdné hledání)."),
    ("trending_queries", s.TrendingQueries, "Aktuálně populární hledané fráze."),
    ("recommend", s.Recommend, "Doporučení produktů daného typu modelu (např. bestsellers)."),
    (
        "content_export",
        s.ContentExport,
        "Export indexovaného obsahu (první stránka, podepsaný privátní API přístup).",
    ),
)


def handler(
    name: str, service: LuigisboxService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: LuigisboxService | None = None,
) -> ConnectorDefinition:
    service = service or LuigisboxService()
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
