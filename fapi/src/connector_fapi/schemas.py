from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
DATETIME_PATTERN = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,63}$"
InvoiceOrder = Literal[
    "id",
    "status",
    "variable_symbol",
    "customer_name",
    "create_date",
    "created_on",
    "payday_date",
    "paid_on",
    "type",
    "total",
    "last_modified_on",
]
ClientOrder = Literal["id", "first_name", "last_name", "email", "phone", "ic", "dic", "ic_dph"]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def valid_date(value: str | None) -> str | None:
    if value is not None:
        date.fromisoformat(value)
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


# Documented limit/offset pagination; invalid values fail instead of clamping.
class Page(Input):
    limit: int = Field(default=20, ge=1, le=100, description="Počet záznamů (max. 100).")
    offset: int = Field(default=0, ge=0, le=100_000, description="Počet přeskočených záznamů.")


class InvoiceList(Page):
    number: str | None = Field(
        default=None, pattern=CODE_PATTERN, description="Číslo faktury."
    )
    variable_symbol: str | None = Field(
        default=None, pattern=r"^[0-9]{1,10}$", description="Variabilní symbol."
    )
    type: str | None = Field(
        default=None,
        pattern=r"^[a-z_]{1,32}$",
        description="Typ dokladu (např. invoice, proforma).",
    )
    status: Literal["cancelled", "paid", "issued"] | None = Field(
        default=None, description="Stav faktury."
    )
    client: ID | None = Field(default=None, description="Id klienta.")
    project: ID | None = Field(default=None, description="Id projektu.")
    form: ID | None = Field(default=None, description="Id formuláře.")
    series: ID | None = Field(default=None, description="Id číselné řady.")
    parent: ID | None = Field(default=None, description="Id nadřazené faktury.")
    last_invoice_id: ID | None = Field(
        default=None, description="Jen faktury s id větším než tato hodnota."
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Hledání v čísle, VS, jménu zákazníka a datech.",
    )
    create_date_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum vystavení od (YYYY-MM-DD)."
    )
    create_date_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum vystavení do (YYYY-MM-DD)."
    )
    payday_date_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum splatnosti od (YYYY-MM-DD)."
    )
    payday_date_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum splatnosti do (YYYY-MM-DD)."
    )
    paid_on_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum úhrady od (YYYY-MM-DD)."
    )
    paid_on_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum úhrady do (YYYY-MM-DD)."
    )
    last_modified_after: str | None = Field(
        default=None,
        pattern=DATETIME_PATTERN,
        description="Jen faktury změněné po tomto čase (YYYY-MM-DD HH:MM:SS).",
    )
    order_by: InvoiceOrder | None = Field(default=None, description="Sloupec řazení.")

    @field_validator(
        "create_date_from",
        "create_date_to",
        "payday_date_from",
        "payday_date_to",
        "paid_on_from",
        "paid_on_to",
    )
    @classmethod
    def dates(cls, value: str | None) -> str | None:
        return valid_date(value)

    @field_validator("last_modified_after")
    @classmethod
    def timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def ranges(self) -> InvoiceList:
        for name in ("create_date", "payday_date", "paid_on"):
            start = getattr(self, f"{name}_from")
            end = getattr(self, f"{name}_to")
            if start is not None and end is not None and start > end:
                raise ValueError("Datum od musí být nejvýše datum do.")
        return self


class InvoiceID(Input):
    invoice_id: ID = Field(description="Id faktury.")


class ClientList(Page):
    email: str | None = Field(
        default=None,
        pattern=r"^[^\s@:]{1,128}@[^\s@:]{1,128}$",
        description="Přesný e-mail klienta.",
    )
    project: ID | None = Field(default=None, description="Id projektu.")
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Hledání ve jménu, firmě, e-mailu, telefonu, IČ a DIČ.",
    )
    order_by: ClientOrder | None = Field(default=None, description="Sloupec řazení.")
    show_statistics: bool = Field(default=False, description="Přidat statistiky klienta.")


class ClientID(Input):
    client_id: ID = Field(description="Id klienta.")
    show_statistics: bool = Field(default=False, description="Přidat statistiky klienta.")


class FormList(Page):
    project: ID | None = Field(default=None, description="Id projektu.")
    search: str | None = Field(
        default=None, min_length=1, max_length=128, description="Hledání v názvu formuláře."
    )
    show_deleted: bool = Field(default=False, description="Zahrnout smazané formuláře.")
    order_by: Literal["id", "name"] | None = Field(default=None, description="Sloupec řazení.")


class FormID(Input):
    form_id: ID = Field(description="Id formuláře.")
    with_payment_methods: bool = Field(
        default=False, description="Přidat dostupné platební metody."
    )


class ItemTemplateList(Page):
    form: ID | None = Field(default=None, description="Jen položky použité ve formuláři.")
    name: str | None = Field(
        default=None, min_length=1, max_length=128, description="Filtr podle názvu položky."
    )
    code: str | None = Field(
        default=None, pattern=CODE_PATTERN, description="Filtr podle kódu položky."
    )


class PaymentList(Page):
    project: ID | None = Field(default=None, description="Id projektu.")
    date: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Datum platby (YYYY-MM-DD)."
    )
    unpaired: bool | None = Field(default=None, description="Jen nespárované platby.")
    unresolved: bool | None = Field(default=None, description="Jen nevyřešené platby.")

    @field_validator("date")
    @classmethod
    def payment_date(cls, value: str | None) -> str | None:
        return valid_date(value)
