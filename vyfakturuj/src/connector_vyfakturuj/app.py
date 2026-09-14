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
from .service import SLUG, VERSION, VyfakturujService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_invoices", s.InvoiceList, "Stránka dokladů s filtrem typu, zákazníka, dat a řazením."),
    ("get_invoice", s.InvoiceID, "Detail dokladu podle ID včetně položek."),
    (
        "list_contacts",
        s.ContactList,
        "Stránka kontaktů adresáře s filtrem IČO, DIČ, názvu a e-mailu.",
    ),
    ("get_contact", s.ContactID, "Detail kontaktu adresáře podle ID."),
    ("get_template", s.TemplateID, "Detail šablony nebo pravidelné faktury podle ID."),
    ("list_payment_methods", s.Input, "Seznam platebních metod účtu."),
    ("list_number_series", s.Input, "Seznam číselných řad účtu."),
    ("list_tags", s.Input, "Seznam štítků účtu."),
)


def handler(
    name: str, service: VyfakturujService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: VyfakturujService | None = None,
) -> ConnectorDefinition:
    service = service or VyfakturujService()
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
