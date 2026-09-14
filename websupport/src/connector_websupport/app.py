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
from .service import SLUG, VERSION, WebsupportService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("list_services", s.PageV1, "Stránkovaný seznam služeb účtu (domény, hostingy) včetně ID."),
    ("get_service", s.ServiceID, "Detail služby podle ID (název, stav, expirace)."),
    ("list_zones", s.PageV1, "Stránkovaný seznam DNS zón účtu."),
    ("get_dns_zone", s.ServiceID, "Detail DNS zóny služby (název, DNSSEC, poslední kontrola)."),
    ("list_dns_records", s.DnsRecordList, "Stránkovaný seznam DNS záznamů služby s filtrem."),
    ("list_ftp_accounts", s.ServicePage, "Stránkovaný seznam FTP účtů hostingu bez hesel."),
    ("get_ftp_account", s.FtpAccountID, "Detail FTP účtu hostingu bez hesla."),
)


def handler(
    name: str, service: WebsupportService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: WebsupportService | None = None) -> ConnectorDefinition:
    service = service or WebsupportService()
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
