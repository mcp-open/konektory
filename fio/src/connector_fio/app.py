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
from .service import SLUG, VERSION, FioService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_transactions", s.Period, "Pohyby na účtu za období nejvýše 90 dní."),
    (
        "last_transactions",
        s.Input,
        "Pohyby od posledního stažení; neprázdná odpověď posune serverovou zarážku Fio.",
    ),
    ("get_statement", s.Statement, "Oficiální výpis pohybů podle roku a čísla výpisu."),
    ("last_statement_number", s.Input, "Rok a číslo posledního vytvořeného oficiálního výpisu."),
)


def handler(
    name: str, service: FioService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: FioService | None = None,
) -> ConnectorDefinition:
    service = service or FioService()
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
