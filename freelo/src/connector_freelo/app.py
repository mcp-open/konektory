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
from .service import SLUG, VERSION, FreeloService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_projects", s.ProjectList, "Seznam vlastních aktivních projektů a tasklistů."),
    ("get_project", s.ProjectID, "Detail projektu včetně dostupných tasklistů a rozpočtu."),
    ("list_tasks", s.TaskList, "Stránka úkolů s filtrem projektu nebo názvu."),
    ("get_task", s.TaskID, "Detail úkolu s omezeným počtem nejnovějších komentářů."),
)


def handler(
    name: str, service: FreeloService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: FreeloService | None = None) -> ConnectorDefinition:
    service = service or FreeloService()
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
