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
from .service import SLUG, VERSION, ComgateService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "get_payment",
        s.TransID,
        "Stav a detail platby Comgate podle transId (stav, částka, měna, metoda, plátce).",
    ),
    (
        "list_transfers",
        s.TransferList,
        "Seznam bankovních převodů (výplat) Comgate uskutečněných v daný den (YYYY-MM-DD).",
    ),
    (
        "get_transfer",
        s.TransferID,
        "Detail bankovního převodu Comgate podle transferId: platby a poplatky v převodu.",
    ),
    (
        "list_methods",
        s.MethodList,
        "Platební metody povolené pro e-shop v Comgate; volitelně filtr jazyk, měna, země.",
    ),
)


def handler(
    name: str, service: ComgateService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: ComgateService | None = None,
) -> ConnectorDefinition:
    service = service or ComgateService()
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
