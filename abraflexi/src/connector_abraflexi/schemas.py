from __future__ import annotations

import math
import re
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Deliberately narrower than the historical 180-evidence registry. Security,
# payroll, server administration and custom SQL evidence are never dispatched.
EVIDENCES = {
    "faktura-vydana": "prodej",
    "faktura-vydana-polozka": "prodej",
    "typ-faktury-vydane": "typy",
    "objednavka-prijata": "prodej",
    "objednavka-prijata-polozka": "prodej",
    "skladovy-pohyb": "sklad",
    "skladovy-pohyb-polozka": "sklad",
    "skladova-karta": "sklad",
    "stav-skladu-k-datu": "sklad",
    "cenik": "sklad",
    "sklad": "sklad",
    "adresar": "partneri",
    "kontakt": "partneri",
    "ucetni-denik": "ucetnictvi",
    "mena": "ciselniky",
}
EvidenceName = Literal[
    "faktura-vydana",
    "faktura-vydana-polozka",
    "typ-faktury-vydane",
    "objednavka-prijata",
    "objednavka-prijata-polozka",
    "skladovy-pohyb",
    "skladovy-pohyb-polozka",
    "skladova-karta",
    "stav-skladu-k-datu",
    "cenik",
    "sklad",
    "adresar",
    "kontakt",
    "ucetni-denik",
    "mena",
]
FieldName = Annotated[
    str, Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,40}(\.[A-Za-z][A-Za-z0-9]{0,40})?$")
]
RecordID = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")
]
DateText = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
Code = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 _.:+-]+$")]
Order = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,40}@[AD]$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_max_length=256)

    @model_validator(mode="before")
    @classmethod
    def dates_and_text(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            raise ValueError("Argumenty musí být objekt.")
        for name, value in values.items():
            if isinstance(value, str):
                if any(ord(char) < 32 or ord(char) == 127 for char in value):
                    raise ValueError("Neplatný text.")
                if name in {"date", "date_from", "date_to"}:
                    date.fromisoformat(value)
        start, end = values.get("date_from"), values.get("date_to")
        if isinstance(start, str) and isinstance(end, str) and start > end:
            raise ValueError("Obrácený rozsah dat.")
        return values


class Filter(Input):
    field: FieldName
    op: Literal[
        "eq",
        "neq",
        "lt",
        "lte",
        "gt",
        "gte",
        "like",
        "begins",
        "ends",
        "is_null",
        "is_not_null",
        "is_true",
        "is_false",
    ]
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def literal(self) -> Filter:
        if self.op.startswith("is_"):
            if self.value is not None:
                raise ValueError("Unární operátor nemá hodnotu.")
        elif self.value is None:
            raise ValueError("Operátor vyžaduje hodnotu.")
        elif isinstance(self.value, str):
            if not re.fullmatch(r"[\w .,:+@-]{1,128}", self.value):
                raise ValueError("Neplatný literál filtru.")
        elif isinstance(self.value, (int, float)) and (
            abs(self.value) > 2**53 - 1 or not math.isfinite(self.value)
        ):
            raise ValueError("Číslo je mimo bezpečný rozsah.")
        if self.op in {"like", "begins", "ends"} and not isinstance(self.value, str):
            raise ValueError("Textový operátor vyžaduje text.")
        return self


class Page(Input):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class EvidenceInput(Input):
    evidence: EvidenceName


class ListRecords(Page, EvidenceInput):
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    order: Order | None = None
    fields: list[FieldName] = Field(default_factory=list, max_length=20)


class GetRecord(EvidenceInput):
    record_id: RecordID
    fields: list[FieldName] = Field(default_factory=list, max_length=20)
    relations: list[Literal["polozky", "vazby"]] = Field(default_factory=list, max_length=2)


class SumRecords(EvidenceInput):
    evidence: Literal["faktura-vydana", "objednavka-prijata", "skladovy-pohyb"]
    filters: list[Filter] = Field(default_factory=list, max_length=8)


class ListEvidences(Input):
    area: Literal["prodej", "typy", "sklad", "partneri", "ucetnictvi", "ciselniky"] | None = None


class Documents(Page):
    date_from: DateText | None = None
    date_to: DateText | None = None
    order: Order | None = None


class Invoices(Documents):
    payment_status: Literal["uhrazeno", "castUhr", "neuhrazeno"] | None = None


class Invoice(Input):
    invoice_id: RecordID
    include_items: bool = True


class Journal(Page):
    invoice_id: Annotated[str, Field(pattern=r"^[0-9]{1,15}$")]


class ReceivedOrder(Input):
    order_id: RecordID
    include_items: bool = True


class Movements(Documents):
    date_from: DateText
    date_to: DateText
    direction: Literal["prijem", "vydej"] | None = None
    warehouse: Code | None = None


class Movement(Input):
    movement_id: RecordID
    include_items: bool = True


class Stock(Page):
    date: DateText
    warehouse: Code


class Products(Page):
    name_contains: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    group: Code | None = None


class Product(Input):
    product_id: RecordID
