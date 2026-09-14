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
from .service import SLUG, VERSION, FinstatService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("get_basic", s.Company, "Základní údaje slovenské firmy podle IČO (registr, adresa, DIČ)."),
    ("get_detail", s.Company, "Detail firmy podle IČO včetně rizikových indikátorů (PREMIUM)."),
    ("get_extended", s.Company, "Rozšířený detail firmy s finančními ukazateli (ELITE)."),
    ("get_ultimate", s.Company, "Kompletní profil firmy včetně osob a dceřiných firem (ULTIMATE)."),
    ("autocomplete", s.Autocomplete, "Našeptávač firem podle názvu nebo části IČO."),
    ("list_statements", s.Company, "Seznam dostupných účetních závěrek firmy podle IČO."),
    ("get_statement", s.Statement, "Detail účetní závěrky firmy za daný rok a šablonu výkazu."),
)


def handler(
    name: str, service: FinstatService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: FinstatService | None = None) -> ConnectorDefinition:
    service = service or FinstatService()
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
