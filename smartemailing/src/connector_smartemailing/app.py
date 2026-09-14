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
from .service import SLUG, VERSION, SmartemailingService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_contacts", s.ContactList, "Stránka kontaktů s výběrem polí, řazením a filtry."),
    ("get_contact", s.ContactDetail, "Detail kontaktu podle ID nebo e-mailu včetně seznamů."),
    ("list_contactlists", s.ContactlistList, "Stránka seznamů kontaktů."),
    ("get_contactlist", s.ContactlistID, "Detail seznamu kontaktů včetně souhrnných metrik."),
    ("list_emails", s.EmailList, "Stránka e-mailů (šablon) bez těla, max. 10 na stránku."),
    ("list_newsletters", s.NewsletterList, "Stránka odeslaných newsletterů s filtrem podle ID."),
    ("newsletter_stats", s.NewsletterStats, "Souhrnné statistiky newsletterů."),
    ("list_customfields", s.CustomfieldList, "Stránka definic vlastních polí kontaktů."),
)


def handler(
    name: str, service: SmartemailingService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: SmartemailingService | None = None,
) -> ConnectorDefinition:
    service = service or SmartemailingService()
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
