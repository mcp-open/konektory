from __future__ import annotations

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
from .service import AbraFlexiService

READ_TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "get_company_info": (s.Input, "Informace pouze o firmě vázané na instalaci."),
    "list_companies": (s.Input, "Vrátí pouze vázanou firmu; neenumeruje ostatní databáze."),
    "list_evidences": (s.ListEvidences, "Pevný katalog podporovaných read-only evidencí."),
    "list_records": (s.ListRecords, "Stránka evidence se strukturovanými AND filtry."),
    "get_record": (s.GetRecord, "Detail evidence; pouze položky a vazby, žádné soubory."),
    "sum_records": (s.SumRecords, "Providerem vypočtený souhrn povolené evidence."),
    "get_evidence_properties": (s.EvidenceInput, "Metadata polí povolené evidence."),
    "list_issued_invoices": (s.Invoices, "Vydané faktury, datum a stav úhrady."),
    "get_issued_invoice": (s.Invoice, "Detail vydané faktury, volitelně s položkami."),
    "list_invoice_types": (s.Page, "Stránka typů vydaných faktur."),
    "get_invoice_journal": (s.Journal, "Účetní deník konkrétního číselného ID faktury."),
    "list_received_orders": (s.Documents, "Přijaté objednávky s datovým filtrem."),
    "get_received_order": (s.ReceivedOrder, "Detail přijaté objednávky."),
    "list_stock_movements": (s.Movements, "Pohyby za období; filtr skladu/směru po stránkách."),
    "get_stock_movement": (s.Movement, "Detail skladového pohybu."),
    "get_stock_status": (s.Stock, "Stav skladu k datu, explicitní sklad a stránkování."),
    "list_products": (s.Products, "Ceník; lokální filtr nad stránkou, pokračuj přes next_offset."),
    "get_product": (s.Product, "Detail produktu podle číselného ID nebo bezpečného kódu."),
}


def build_definition(service: AbraFlexiService | None = None) -> ConnectorDefinition:
    service = service or AbraFlexiService()

    def spec(name: str, model: type[BaseModel], description: str) -> ToolSpec:
        async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
            return await service.invoke(name, arguments, context)

        return ToolSpec(name, model, invoke, description)

    return ConnectorDefinition(
        slug="abraflexi",
        version="1.0.0",
        requires_secret=True,
        tools={name: spec(name, *definition) for name, definition in READ_TOOLS.items()},
        test_connection=service.test_connection,
        close=service.close,
    )


def create_runtime_app(*, signing_key: str | None = None) -> Starlette:
    return create_app(build_definition(), signing_key=signing_key)
