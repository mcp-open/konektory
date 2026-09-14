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
from .service import SLUG, VERSION, GopayService

# Every published tool: name, closed argument model, model-facing description.
# Keep this tuple, connector.yaml and tests/test_contract.py in sync.
SPECS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "get_payment",
        s.PaymentID,
        "Stav a detail platby GoPay podle ID platby (stav, částka, měna, metoda, plátce).",
    ),
    (
        "list_refunds",
        s.PaymentID,
        "Historie refundací platby GoPay podle ID platby.",
    ),
    (
        "get_card",
        s.CardID,
        "Detail uložené platební karty GoPay podle card_id (maskovaný PAN, stav, expirace).",
    ),
    (
        "list_payment_instruments",
        s.PaymentInstruments,
        "Povolené platební metody a banky pro GoID instalace; volitelně jen pro jednu měnu.",
    ),
)


def handler(
    name: str, service: GopayService
) -> Callable[[BaseModel, InvocationContext], Awaitable[ToolEnvelope]]:
    async def invoke(arguments: BaseModel, context: InvocationContext) -> ToolEnvelope:
        return await service.invoke(name, arguments, context)

    return invoke


def build_definition(
    service: GopayService | None = None,
) -> ConnectorDefinition:
    service = service or GopayService()
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
