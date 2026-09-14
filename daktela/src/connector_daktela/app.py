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
from .service import SLUG, VERSION, DaktelaService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "list_tickets",
        s.TicketList,
        "Stránka tiketů s filtrem fáze, priority, kategorie, uživatele, kontaktu, názvu a data.",
    ),
    ("get_ticket", s.TicketName, "Detail tiketu podle jeho unikátního jména (čísla)."),
    (
        "list_activities",
        s.ActivityList,
        "Stránka aktivit (hovory, e-maily, chaty) s filtrem tiketu, typu, stavu, fronty a agenta.",
    ),
    ("list_contacts", s.ContactList, "Stránka kontaktů s filtrem příjmení a účtu."),
    ("get_contact", s.ContactName, "Detail kontaktu podle jeho unikátního jména."),
    ("list_queues", s.QueueList, "Stránka front (queues) s volitelným filtrem typu."),
    ("list_users", s.UserList, "Stránka uživatelů (agentů) instance."),
)


def handler(
    name: str, service: DaktelaService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: DaktelaService | None = None,
) -> ConnectorDefinition:
    service = service or DaktelaService()
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
