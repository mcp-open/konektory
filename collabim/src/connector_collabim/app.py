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
from .service import SLUG, VERSION, CollabimService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_projects", s.ProjectList, "Stránka projektů s filtrem názvu a aktivity."),
    ("get_project", s.ProjectID, "Detail projektu (název, web, štítky)."),
    ("list_keywords", s.KeywordList, "Stránka klíčových slov projektu s aktuálními pozicemi."),
    (
        "keyword_positions",
        s.KeywordPositions,
        "Historie pozic vybraných klíčových slov (podle ID nebo štítku) v ohraničeném intervalu.",
    ),
    (
        "aggregated_positions",
        s.AggregatedPositions,
        "Agregované pozice všech klíčových slov projektu v ohraničeném intervalu.",
    ),
    (
        "position_distribution",
        s.PositionDistribution,
        "Rozložení pozic (TOP3, 4–10, 11–20, 21+) projektu v ohraničeném intervalu.",
    ),
    (
        "market_share",
        s.MarketShare,
        "Podíl na trhu (konkurenční domény) pro projekt a vyhledávač v ohraničeném intervalu.",
    ),
    (
        "list_activities",
        s.ActivityList,
        "Stránka aktivit (linkbuilding) s filtrem projektu a data.",
    ),
)


def handler(
    name: str, service: CollabimService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: CollabimService | None = None,
) -> ConnectorDefinition:
    service = service or CollabimService()
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
