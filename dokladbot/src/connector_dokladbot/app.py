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
from .service import SLUG, VERSION, DokladbotService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_documents",
        s.Documents,
        "Stránka přijatých i vydaných dokladů a jejich stavu schválení.",
    ),
    ("get_document", s.DocumentID, "Detail dokladu včetně rozpisu DPH podle sazeb."),
    ("get_vat_summary", s.VatPeriod, "Souhrn DPH za rok, čtvrtletí nebo měsíc: základy a daň."),
    ("list_due_invoices", s.DueDates, "Faktury po splatnosti nebo s blížící se splatností."),
    ("get_overview", s.Input, "Přehled hospodaření: příjmy, výdaje a počty dokladů."),
    ("get_credit_balance", s.Input, "Aktuální zůstatek kreditů účtu."),
    ("list_companies", s.Input, "Firmy na účtu včetně IČO a DIČ."),
    ("list_suppliers", s.Suppliers, "Dodavatelé podle útraty."),
)


def handler(
    name: str, service: DokladbotService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: DokladbotService | None = None,
) -> ConnectorDefinition:
    service = service or DokladbotService()
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
