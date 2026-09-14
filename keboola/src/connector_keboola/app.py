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
from .service import SLUG, VERSION, KeboolaService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("verify_token", s.Input, "Ověření Storage tokenu: projekt, oprávnění a vlastník."),
    ("list_buckets", s.BucketList, "Seznam bucketů projektu, volitelně s metadaty."),
    (
        "list_tables",
        s.TableList,
        "Seznam tabulek v projektu nebo v jednom bucketu, volitelně se sloupci.",
    ),
    ("get_table", s.TableID, "Detail tabulky: sloupce, primární klíč, velikost, import."),
    (
        "preview_table",
        s.TablePreview,
        "Náhled dat tabulky (nejvýše 100 řádků, volitelný výběr sloupců).",
    ),
    ("list_components", s.ComponentList, "Komponenty s konfiguracemi v projektu podle typu."),
    ("list_configurations", s.ComponentID, "Seznam konfigurací jedné komponenty."),
    ("list_jobs", s.JobList, "Seznam Storage jobů projektu se stránkováním limit/offset."),
)


def handler(
    name: str, service: KeboolaService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: KeboolaService | None = None,
) -> ConnectorDefinition:
    service = service or KeboolaService()
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
