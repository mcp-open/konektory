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
from .service import SLUG, VERSION, SupportboxService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_mail_tickets",
        s.MailTicketList,
        "Stránka e-mailových tiketů s filtrem stavu, schránky, uživatele, štítku a data.",
    ),
    ("get_mail_ticket", s.MailTicketID, "Detail (hlavička) e-mailového tiketu podle ID."),
    (
        "list_mail_ticket_messages",
        s.MailTicketMessages,
        "Stránka zpráv jednoho e-mailového tiketu (obsah konverzace).",
    ),
    ("list_mailboxes", s.MailboxList, "Stránka e-mailových schránek dostupných API klíči."),
    ("list_users", s.UserList, "Stránka uživatelů (agentů) účtu."),
    ("list_tags", s.TagList, "Stránka štítků."),
)


def handler(
    name: str, service: SupportboxService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: SupportboxService | None = None,
) -> ConnectorDefinition:
    service = service or SupportboxService()
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
