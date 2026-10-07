from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


DocumentId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class Documents(Input):
    page: int = Field(default=1, ge=1, le=10_000)
    limit: int = Field(default=20, ge=1, le=50)
    status: Literal["PENDING", "APPROVED", "REJECTED", "ALL"] = "ALL"


class DocumentID(Input):
    document_id: DocumentId


class VatPeriod(Input):
    year: int = Field(ge=2020, le=2100)
    quarter: int | None = Field(default=None, ge=1, le=4)
    month: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def one_period(self) -> VatPeriod:
        if self.quarter is not None and self.month is not None:
            raise ValueError("Zadejte čtvrtletí, nebo měsíc, ne obojí.")
        return self


class DueDates(Input):
    status: Literal["overdue", "upcoming"] = "overdue"
    days: int = Field(default=30, ge=1, le=365)


class Suppliers(Input):
    limit: int = Field(default=20, ge=1, le=100)
