from __future__ import annotations

from openmcp_connector_runtime import (
    ConnectorDefinition,
    InvocationContext,
    SecretResolver,
    ToolEnvelope,
    ToolSpec,
    create_app,
)
from pydantic import BaseModel
from starlette.applications import Starlette

from .schemas import EmptyInput, ListOrdersInput, SalesSummaryInput
from .service import DotykackaService


def build_definition(service: DotykackaService | None = None) -> ConnectorDefinition:
    service = service or DotykackaService()

    async def cloud_handler(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.get_cloud_info(EmptyInput.model_validate(arguments), context)

    async def orders_handler(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.list_orders(ListOrdersInput.model_validate(arguments), context)

    async def summary_handler(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.sales_summary(SalesSummaryInput.model_validate(arguments), context)

    return ConnectorDefinition(
        slug="dotykacka",
        version="1.0.0",
        tools={
            "get_cloud_info": ToolSpec(
                "get_cloud_info", EmptyInput, cloud_handler, "Informace o připojeném cloudu."
            ),
            "list_orders": ToolSpec(
                "list_orders", ListOrdersInput, orders_handler, "Seznam účtenek a objednávek."
            ),
            "sales_summary": ToolSpec(
                "sales_summary",
                SalesSummaryInput,
                summary_handler,
                "Omezený souhrn prodejů.",
            ),
        },
        test_connection=service.test_connection,
        requires_secret=True,
        close=service.close,
    )


def create_runtime_app(
    *, resolver: SecretResolver | None = None, signing_key: str | None = None
) -> Starlette:
    return create_app(build_definition(DotykackaService(resolver)), signing_key=signing_key)
