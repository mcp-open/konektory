from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
DAY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Day = Annotated[str, Field(pattern=DAY_PATTERN, min_length=10, max_length=10)]
Text = Annotated[str, Field(min_length=1, max_length=128)]
# Documented bit values of the ``type`` filter (Typ dokladu).
DocumentType = Literal[1, 2, 4, 8, 16, 32, 64, 128, 512]
InvoiceSort = Literal["id", "number", "date_created", "date_due", "date_paid"]
ContactSort = Literal["id", "name", "IC"]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def day(value: str) -> str:
    """Validate a `YYYY-MM-DD` calendar day; the pattern alone accepts 2024-99-99."""
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


# Provider pagination (`rows_limit`/`rows_offset`); invalid values fail, never clamp.
class Page(Input):
    rows_limit: int = Field(default=20, ge=1, le=100, description="Počet záznamů na stránku")
    rows_offset: int = Field(default=0, ge=0, le=10_000, description="Počet přeskočených záznamů")


class InvoiceList(Page):
    type: DocumentType | None = Field(
        default=None,
        description=(
            "Typ dokladu: 1 faktura, 2 zálohová, 4 proforma, 8 výzva k platbě, 16 daňový "
            "doklad, 32 opravný daňový doklad, 64 příjmový doklad, 128 opravný doklad, "
            "512 objednávka"
        ),
    )
    flags: int | None = Field(
        default=None,
        ge=1,
        le=2**17 - 1,
        description="Bitová maska příznaků (2 uhrazeno, 8 storno, 64 nedoplatek, …)",
    )
    id_customer: ID | None = Field(default=None, description="ID kontaktu v adresáři")
    id_number_series: ID | None = Field(default=None, description="ID číselné řady")
    id_tag: ID | None = Field(default=None, description="ID štítku")
    id_parent: ID | None = Field(default=None, description="ID nadřazeného dokladu")
    number: Text | None = Field(default=None, description="Číslo dokladu")
    variable_symbol: str | None = Field(
        default=None, pattern=r"^[0-9]{1,10}$", description="Variabilní symbol"
    )
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$", description="Měna (ISO 4217)")
    date_created_from: Day | None = Field(
        default=None, description="Datum vytvoření od (YYYY-MM-DD)"
    )
    date_created_to: Day | None = Field(default=None, description="Datum vytvoření do (YYYY-MM-DD)")
    date_due: Day | None = Field(default=None, description="Datum splatnosti (YYYY-MM-DD)")
    date_paid: Day | None = Field(default=None, description="Datum úhrady (YYYY-MM-DD)")
    q: Text | None = Field(default=None, description="Fulltextové hledání")
    sort_by: InvoiceSort | None = Field(default=None, description="Sloupec řazení")
    sort_dir: Literal["asc", "desc"] = Field(default="desc", description="Směr řazení")

    @model_validator(mode="after")
    def valid_days(self) -> InvoiceList:
        for value in (self.date_created_from, self.date_created_to, self.date_due, self.date_paid):
            if value is not None:
                day(value)
        if (
            self.date_created_from is not None
            and self.date_created_to is not None
            and self.date_created_to < self.date_created_from
        ):
            raise ValueError("date_created_to musí být >= date_created_from.")
        return self


class InvoiceID(Input):
    invoice_id: ID = Field(description="ID faktury")


class ContactList(Page):
    ic: str | None = Field(default=None, pattern=r"^[0-9]{6,10}$", description="IČO")
    dic: str | None = Field(default=None, pattern=r"^[A-Z]{2}[0-9A-Z]{2,13}$", description="DIČ")
    name: Text | None = Field(default=None, description="Název v adresáři")
    mail_to: str | None = Field(
        default=None,
        min_length=3,
        max_length=128,
        pattern=r"^[^\s@]+@[^\s@]+$",
        description="E-mail pro faktury",
    )
    q: Text | None = Field(default=None, description="Fulltextové hledání")
    sort_by: ContactSort | None = Field(default=None, description="Sloupec řazení")
    sort_dir: Literal["asc", "desc"] = Field(default="asc", description="Směr řazení")


class ContactID(Input):
    contact_id: ID = Field(description="ID kontaktu")


class TemplateID(Input):
    template_id: ID = Field(description="ID šablony / pravidelné faktury")
