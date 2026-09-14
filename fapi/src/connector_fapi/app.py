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
from .service import SLUG, VERSION, FapiService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_invoices",
        s.InvoiceList,
        "Stránka faktur s filtrem stavu, typu, klienta, formuláře, dat a hledání.",
    ),
    ("get_invoice", s.InvoiceID, "Detail faktury včetně položek, plateb a zákazníka."),
    ("list_clients", s.ClientList, "Stránka klientů s filtrem e-mailu, projektu a hledání."),
    ("get_client", s.ClientID, "Detail klienta podle Id, volitelně se statistikami."),
    ("list_forms", s.FormList, "Stránka prodejních formulářů s filtrem projektu a názvu."),
    ("get_form", s.FormID, "Detail formuláře, volitelně s platebními metodami."),
    (
        "list_item_templates",
        s.ItemTemplateList,
        "Stránka šablon položek (produktů) s filtrem názvu, kódu a formuláře.",
    ),
    ("list_payments", s.PaymentList, "Stránka přijatých plateb s filtrem data a spárování."),
)


def handler(
    name: str, service: FapiService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: FapiService | None = None,
) -> ConnectorDefinition:
    service = service or FapiService()
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
