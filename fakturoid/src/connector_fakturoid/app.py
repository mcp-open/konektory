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
from .service import SLUG, VERSION, FakturoidService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_invoices", s.Page, "Stránka vydaných dokladů a stavů úhrad."),
    ("get_invoice", s.InvoiceID, "Detail vydaného dokladu podle ID."),
    ("list_subjects", s.Page, "Stránka kontaktů a firem v adresáři."),
    ("get_subject", s.SubjectID, "Detail kontaktu nebo firmy podle ID."),
    ("list_expenses", s.Page, "Stránka nákladů a přijatých dokladů."),
    ("get_expense", s.ExpenseID, "Detail nákladu podle ID."),
)


def handler(
    name: str, service: FakturoidService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: FakturoidService | None = None) -> ConnectorDefinition:
    service = service or FakturoidService()
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
