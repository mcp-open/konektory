from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
# Documented filter operators (``Filtering data`` section of the v3 help).
Operator = Literal["eq", "!eq", "ct", "!ct", "lt", "lte", "gt", "gte"]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


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


class Filter(Input):
    field: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9]{0,63}$",
        description="Název sloupce povolený pro daný seznam (např. DateOfIssue, PartnerId).",
    )
    operator: Operator = Field(default="eq", description="Operátor filtru iDoklad.")
    value: str = Field(
        min_length=1,
        max_length=128,
        description="Hodnota; text se posílá base64 kódovaný, čísla a data přímo.",
    )


class Page(Input):
    page: int = Field(default=1, ge=1, le=10_000, description="Číslo stránky (od 1).")
    page_size: int = Field(default=20, ge=1, le=100, description="Velikost stránky (max. 100).")
    filters: list[Filter] | None = Field(
        default=None, max_length=8, description="Podmínky filtru spojené podle filter_type."
    )
    filter_type: Literal["and", "or"] = Field(
        default="and", description="Spojení více podmínek filtru."
    )
    sort_by: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9]{0,63}$",
        description="Sloupec řazení povolený pro daný seznam.",
    )
    sort_order: Literal["asc", "desc"] = Field(default="asc", description="Směr řazení.")


class IssuedInvoiceList(Page):
    pass


class ReceivedInvoiceList(Page):
    pass


class ContactList(Page):
    pass


class BankStatementList(Page):
    pass


class PaymentList(Page):
    pass


class IssuedInvoiceID(Input):
    invoice_id: ID = Field(description="Id vydané faktury.")


class ContactID(Input):
    contact_id: ID = Field(description="Id kontaktu.")


class Empty(Input):
    pass
