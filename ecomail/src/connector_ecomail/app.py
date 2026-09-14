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
from .service import SLUG, VERSION, EcomailService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_lists", s.Empty, "Všechny seznamy kontaktů v účtu."),
    ("get_list", s.ListID, "Detail seznamu včetně vlastních polí, skupin a segmentů."),
    ("list_subscribers", s.SubscriberList, "Stránka kontaktů seznamu s filtrem stavu."),
    ("get_subscriber", s.SubscriberDetail, "Detail kontaktu podle e-mailu (globálně/v seznamu)."),
    ("list_campaigns", s.CampaignList, "Kampaně s filtry, řazením a stránkováním."),
    ("campaign_stats", s.CampaignStats, "Souhrnné statistiky kampaně, volitelně v rozsahu dat."),
    ("list_templates", s.Empty, "Všechny e-mailové šablony v účtu."),
    ("list_automations", s.Empty, "Všechny automatizace (pipelines) v účtu."),
)


def handler(
    name: str, service: EcomailService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: EcomailService | None = None,
) -> ConnectorDefinition:
    service = service or EcomailService()
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
