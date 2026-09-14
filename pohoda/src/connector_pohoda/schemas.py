from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
DAY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Day = Annotated[str, Field(pattern=DAY_PATTERN, min_length=10, max_length=10)]
STAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
Stamp = Annotated[str, Field(pattern=STAMP_PATTERN, min_length=19, max_length=19)]
Ico = Annotated[str, Field(pattern=r"^[0-9]{6,15}$")]
MAX_RANGE = timedelta(days=366)
# ``invoiceType`` / ``orderType`` attributes of listInvoiceRequest / listOrderRequest
# (invoice.xsd invoiceTypeType, order.xsd orderTypeType).
InvoiceType = Literal[
    "issuedInvoice",
    "issuedCreditNotice",
    "issuedDebitNote",
    "issuedAdvanceInvoice",
    "receivable",
    "issuedProformaInvoice",
    "penalty",
    "issuedCorrectiveTax",
    "receivedInvoice",
    "receivedCreditNotice",
    "receivedDebitNote",
    "receivedAdvanceInvoice",
    "commitment",
    "receivedProformaInvoice",
    "receivedCorrectiveTax",
    "business",
]
OrderType = Literal["issuedOrder", "receivedOrder"]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def day(value: str) -> date:
    return date.fromisoformat(value)


def stamp(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    return value


# Closed argument object: unknown keys, raw URLs, control characters and
# type coercion are rejected before any handler runs. Keep models docstring-free;
# a docstring would change the JSON schema published in connector.yaml.
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_max_length=256)

    @model_validator(mode="before")
    @classmethod
    def text_values(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            raise ValueError("Argumenty musí být objekt.")
        if any(isinstance(value, str) and not valid_text(value) for value in values.values()):
            raise ValueError("Neplatný textový argument.")
        return values


# POHODA ``limit`` element (idFrom, count 1..10000); the adapter caps count at 500
# because every record is converted, validated and pseudonymised in memory.
class Limit(Input):
    count: int = Field(default=50, ge=1, le=500, description="Nejvyšší počet záznamů (limit/count)")
    id_from: ID | None = Field(
        default=None, description="Vrátit záznamy s ID od této hodnoty (limit/idFrom)"
    )


class DocumentList(Limit):
    date_from: Day | None = Field(default=None, description="Datum dokladu od (YYYY-MM-DD)")
    date_till: Day | None = Field(
        default=None, description="Datum dokladu do (YYYY-MM-DD), nejvýše 366 dní"
    )
    company: str | None = Field(
        default=None, min_length=1, max_length=255, description="Firma partnera (selectedCompanys)"
    )
    ico: Ico | None = Field(default=None, description="IČ partnera (selectedIco)")
    last_changes: Stamp | None = Field(
        default=None, description="Jen záznamy změněné od (YYYY-MM-DDThh:mm:ss)"
    )

    @model_validator(mode="after")
    def bounded(self) -> DocumentList:
        first = day(self.date_from) if self.date_from else None
        last = day(self.date_till) if self.date_till else None
        if first and last and (last < first or last - first > MAX_RANGE):
            raise ValueError("Období musí být neprázdné a nejvýše 366 dní dlouhé.")
        if self.last_changes:
            stamp(self.last_changes)
        return self


class InvoiceList(DocumentList):
    invoice_type: InvoiceType = Field(
        default="issuedInvoice", description="Agenda faktur (atribut invoiceType)"
    )


class OrderList(DocumentList):
    order_type: OrderType = Field(
        default="receivedOrder", description="Agenda objednávek (atribut orderType)"
    )


class PartnerList(Limit):
    company: str | None = Field(default=None, min_length=1, max_length=255, description="Firma")
    name: str | None = Field(default=None, min_length=1, max_length=32, description="Jméno")
    city: str | None = Field(default=None, min_length=1, max_length=45, description="Obec")
    ico: Ico | None = Field(default=None, description="IČ")
    last_changes: Stamp | None = Field(
        default=None, description="Jen záznamy změněné od (YYYY-MM-DDThh:mm:ss)"
    )

    @model_validator(mode="after")
    def valid_stamp(self) -> PartnerList:
        if self.last_changes:
            stamp(self.last_changes)
        return self


class StockList(Limit):
    code: str | None = Field(default=None, min_length=1, max_length=64, description="Kód zásoby")
    ean: str | None = Field(default=None, pattern=r"^[0-9]{8,14}$", description="Čárový kód (EAN)")
    name: str | None = Field(default=None, min_length=1, max_length=128, description="Název zásoby")
    last_changes: Stamp | None = Field(
        default=None, description="Jen záznamy změněné od (YYYY-MM-DDThh:mm:ss)"
    )

    @model_validator(mode="after")
    def valid_stamp(self) -> StockList:
        if self.last_changes:
            stamp(self.last_changes)
        return self


class InvoiceID(Input):
    invoice_id: ID = Field(description="ID dokladu v POHODA")
    invoice_type: InvoiceType = Field(
        default="issuedInvoice", description="Agenda faktur (atribut invoiceType)"
    )


class OrderID(Input):
    order_id: ID = Field(description="ID objednávky v POHODA")
    order_type: OrderType = Field(
        default="receivedOrder", description="Agenda objednávek (atribut orderType)"
    )


class PartnerID(Input):
    partner_id: ID = Field(description="ID záznamu adresáře v POHODA")


class StockID(Input):
    stock_id: ID = Field(description="ID zásoby v POHODA")
