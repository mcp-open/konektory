from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
ExtendedField = Literal["Items", "VatBreakdowns", "Payments", "LinkedDocuments"]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def valid_date(value: str | None) -> str | None:
    if value is not None:
        date.fromisoformat(value)
    return value


def valid_timestamp(value: str | None) -> str | None:
    if value is not None:
        datetime.fromisoformat(value)
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


# Documented Top/Skip pagination; Top is capped at 100 by the provider.
class Page(Input):
    top: int = Field(default=50, ge=1, le=100, description="Počet záznamů (max. 100).")
    skip: int = Field(default=0, ge=0, le=100_000, description="Počet přeskočených záznamů.")
    last_modified_from: str | None = Field(
        default=None,
        pattern=TIMESTAMP_PATTERN,
        description="Jen záznamy změněné od tohoto času (ISO 8601, např. 2026-01-31T00:00:00).",
    )

    @field_validator("last_modified_from")
    @classmethod
    def timestamp(cls, value: str | None) -> str | None:
        return valid_timestamp(value)


class DocumentList(Page):
    issue_date_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum vystavení od (YYYY-MM-DD)."
    )
    issue_date_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum vystavení do (YYYY-MM-DD)."
    )
    payment_status: Literal["not_paid", "fully_paid"] | None = Field(
        default=None, description="Stav úhrady."
    )
    numbering_sequence: str | None = Field(
        default=None, min_length=1, max_length=5, description="Číselná řada (max. 5 znaků)."
    )
    document_number_from: str | None = Field(
        default=None, min_length=1, max_length=20, description="Číslo dokladu od."
    )
    document_number_to: str | None = Field(
        default=None, min_length=1, max_length=20, description="Číslo dokladu do."
    )

    @field_validator("issue_date_from", "issue_date_to")
    @classmethod
    def dates(cls, value: str | None) -> str | None:
        return valid_date(value)

    @model_validator(mode="after")
    def ordered(self) -> DocumentList:
        if (
            self.issue_date_from is not None
            and self.issue_date_to is not None
            and self.issue_date_from > self.issue_date_to
        ):
            raise ValueError("Datum od musí být nejvýše datum do.")
        return self


class InvoiceList(DocumentList):
    delivery_date_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum dodání od (YYYY-MM-DD)."
    )
    delivery_date_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum dodání do (YYYY-MM-DD)."
    )
    due_date_status: Literal["in_due", "over_due"] | None = Field(
        default=None, description="Stav splatnosti."
    )
    order_number: str | None = Field(
        default=None, min_length=1, max_length=20, description="Číslo objednávky."
    )
    extended_fields: list[ExtendedField] | None = Field(
        default=None,
        max_length=4,
        description="Rozšířená pole (Items, VatBreakdowns, Payments, LinkedDocuments).",
    )

    @field_validator("delivery_date_from", "delivery_date_to")
    @classmethod
    def delivery_dates(cls, value: str | None) -> str | None:
        return valid_date(value)


class ProformaInvoiceList(DocumentList):
    due_date_status: Literal["in_due", "over_due"] | None = Field(
        default=None, description="Stav splatnosti."
    )
    order_number: str | None = Field(
        default=None, min_length=1, max_length=20, description="Číslo objednávky."
    )
    extended_fields: list[ExtendedField] | None = Field(
        default=None,
        max_length=4,
        description="Rozšířená pole (Items, VatBreakdowns, Payments, LinkedDocuments).",
    )


class ExpenseList(DocumentList):
    due_date_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum splatnosti od (YYYY-MM-DD)."
    )
    due_date_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum splatnosti do (YYYY-MM-DD)."
    )

    @field_validator("due_date_from", "due_date_to")
    @classmethod
    def due_dates(cls, value: str | None) -> str | None:
        return valid_date(value)


class CatalogItemList(Page):
    item_code: str | None = Field(
        default=None, min_length=1, max_length=25, description="Kód položky katalogu."
    )
    name: str | None = Field(
        default=None, min_length=1, max_length=255, description="Fulltext v názvu položky."
    )
    only_marked_for_eshop: bool = Field(
        default=False, description="Jen položky označené pro e-shop."
    )


class PaymentList(Page):
    payment_date_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum platby od (YYYY-MM-DD)."
    )
    payment_date_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum platby do (YYYY-MM-DD)."
    )
    account_id: ID | None = Field(default=None, description="Id finančního účtu.")
    external_id: str | None = Field(
        default=None, min_length=1, max_length=64, description="Externí id platby."
    )

    @field_validator("payment_date_from", "payment_date_to")
    @classmethod
    def payment_dates(cls, value: str | None) -> str | None:
        return valid_date(value)


class InvoiceID(Input):
    invoice_id: ID = Field(description="Id faktury.")


class ExpenseID(Input):
    expense_id: str = Field(pattern=UUID_PATTERN, description="Guid výdajového dokladu.")


class Empty(Input):
    pass
