from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, get_type_hints

from openmcp_connector_runtime import (
    ConnectorDefinition,
    InvocationContext,
    ToolEnvelope,
    ToolSpec,
    create_app,
)
from pydantic import BaseModel, create_model
from starlette.applications import Starlette

from . import tools
from .schemas import Input
from .service import UpgatesService

READ_TOOLS = (
    tools.list_orders,
    tools.get_order_history,
    tools.list_order_statuses,
    tools.list_invoices,
    tools.list_products,
    tools.list_products_simple,
    tools.list_customers,
    tools.list_categories,
    tools.list_labels,
    tools.list_availabilities,
    tools.list_manufacturers,
    tools.list_parameters,
    tools.list_carts,
    tools.list_vouchers,
    tools.list_shipments,
    tools.list_payments,
    tools.list_webhooks,
    tools.list_webhook_events,
    tools.get_languages,
    tools.get_shop_config,
    tools.get_shop_owner,
    tools.get_api_status,
    tools.list_pricelists,
)


def _spec(
    function: Callable[..., Awaitable[ToolEnvelope]],
    service: UpgatesService,
) -> ToolSpec:
    annotations = get_type_hints(function, include_extras=True)
    fields: dict[str, Any] = {
        name: (
            annotations[name],
            ... if parameter.default is inspect.Parameter.empty else parameter.default,
        )
        for name, parameter in inspect.signature(function).parameters.items()
        if name not in {"service", "context"}
    }
    model = create_model(function.__name__ + "Input", __base__=Input, **fields)

    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await function(**arguments.model_dump(), service=service, context=context)

    return ToolSpec(function.__name__, model, invoke, inspect.getdoc(function) or function.__name__)


def build_definition(service: UpgatesService | None = None) -> ConnectorDefinition:
    service = service or UpgatesService()
    return ConnectorDefinition(
        slug="upgates",
        version="1.0.0",
        requires_secret=True,
        tools={function.__name__: _spec(function, service) for function in READ_TOOLS},
        test_connection=service.test_connection,
        close=service.close,
    )


def create_runtime_app(*, signing_key: str | None = None) -> Starlette:
    return create_app(build_definition(), signing_key=signing_key)
