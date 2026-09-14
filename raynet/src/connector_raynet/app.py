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
from .service import RaynetService

SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("raynet_whoami", s.Input, "Informace o připojeném provider účtu s povinnou PII ochranou."),
    ("raynet_resources", s.Input, "Pevný seznam podporovaných zdrojů a číselníků adaptéru."),
    (
        "search_companies",
        s.CompanySearch,
        "Stránka klientů filtrovaná podle jména, IČO a vlastníka.",
    ),
    ("get_company", s.CompanyID, "Detail klienta podle ID."),
    ("get_company_by_ext", s.ExtID, "Detail klienta podle externího ID."),
    (
        "search_persons",
        s.PersonSearch,
        "Stránka kontaktních osob; kontaktní údaje jsou pseudonymizované.",
    ),
    ("get_person", s.PersonID, "Detail kontaktní osoby podle ID."),
    ("get_person_by_ext", s.ExtID, "Detail kontaktní osoby podle externího ID."),
    (
        "search_business_cases",
        s.BusinessCaseSearch,
        "Stránka obchodních případů podle klienta a stavu.",
    ),
    ("get_business_case", s.BusinessCaseID, "Detail obchodního případu podle ID."),
    ("search_leads", s.LeadSearch, "Stránka leadů podle jména, stavu a vlastníka."),
    ("get_lead", s.LeadID, "Detail leadu podle ID."),
    (
        "raynet_list",
        s.GenericList,
        "Jedna stránka podporovaného zdroje, jen pevně povolené filtry.",
    ),
    ("raynet_get", s.GenericGet, "Detail podporovaného zdroje podle ID."),
    ("raynet_get_by_ext", s.GenericExt, "Detail podporovaného zdroje podle externího ID."),
    ("raynet_codebook", s.CodebookList, "Jedna stránka podporovaného číselníku."),
)


def handler(
    name: str, service: RaynetService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(service: RaynetService | None = None) -> ConnectorDefinition:
    service = service or RaynetService()
    return ConnectorDefinition(
        slug="raynet",
        version="1.0.0",
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
