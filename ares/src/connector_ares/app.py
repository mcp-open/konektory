from __future__ import annotations

from openmcp_connector_runtime import (
    ConnectorDefinition,
    InvocationContext,
    ToolEnvelope,
    ToolSpec,
    create_app,
)
from pydantic import BaseModel
from starlette.applications import Starlette

from .schemas import LookupInput, SearchInput
from .service import AresService


def build_definition(service: AresService | None = None) -> ConnectorDefinition:
    service = service or AresService()

    async def lookup_handler(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.lookup(LookupInput.model_validate(arguments), context)

    async def search_handler(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.search(SearchInput.model_validate(arguments), context)

    return ConnectorDefinition(
        slug="ares",
        version="1.0.0",
        tools={
            "ares_subjekt_lookup": ToolSpec(
                "ares_subjekt_lookup",
                LookupInput,
                lookup_handler,
                "Detail ekonomického subjektu podle IČO.",
            ),
            "ares_subjekt_vyhledat": ToolSpec(
                "ares_subjekt_vyhledat",
                SearchInput,
                search_handler,
                "Hledání ekonomických subjektů podle obchodního jména.",
            ),
        },
        close=service.close,
    )


def create_runtime_app(*, signing_key: str | None = None) -> Starlette:
    return create_app(build_definition(), signing_key=signing_key)
