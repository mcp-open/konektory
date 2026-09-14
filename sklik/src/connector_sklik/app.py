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
from .service import SLUG, VERSION, SklikService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("get_client", s.Input, "Informace o účtu Sklik včetně spravovaných (cizích) účtů."),
    ("list_campaigns", s.CampaignList, "Stránka kampaní účtu (ID, název, stav, typ)."),
    ("list_groups", s.GroupList, "Stránka sestav, volitelně jen z vybraných kampaní."),
    (
        "list_keywords",
        s.KeywordList,
        "Stránka klíčových slov, volitelně jen z vybraných sestav nebo kampaní.",
    ),
    (
        "list_ads",
        s.AdList,
        "Stránka inzerátů, volitelně jen z vybraných sestav nebo kampaní.",
    ),
    (
        "campaign_stats",
        s.CampaignStats,
        "Statistiky kampaní (imprese, kliky, CTR, cena, konverze) za ohraničené období.",
    ),
)


def handler(
    name: str, service: SklikService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: SklikService | None = None,
) -> ConnectorDefinition:
    service = service or SklikService()
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
