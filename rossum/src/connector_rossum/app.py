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
from .service import SLUG, VERSION, RossumService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_workspaces", s.WorkspaceList, "Seznam workspaces organizace s filtrem názvu."),
    ("list_queues", s.QueueList, "Seznam front (queues) s filtrem workspace a názvu."),
    ("get_queue", s.QueueID, "Detail fronty včetně nastavení automatizace a schématu."),
    (
        "list_annotations",
        s.AnnotationList,
        "Stránka anotací (dokumentů ke zpracování) s filtrem fronty, stavu a fulltextu.",
    ),
    ("get_annotation", s.AnnotationID, "Detail anotace: stav, fronta, dokument, časové značky."),
    (
        "get_annotation_content",
        s.AnnotationID,
        "Vytěžená data anotace (sekce a datapointy) jen pro čtení.",
    ),
    ("list_documents", s.DocumentList, "Seznam nahraných dokumentů s filtrem názvu souboru."),
)


def handler(
    name: str, service: RossumService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: RossumService | None = None,
) -> ConnectorDefinition:
    service = service or RossumService()
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
