from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]


def valid_text(value: str) -> bool:
    return bool(value) and len(value) <= 256 and not any(
        ord(char) < 32 or ord(char) == 127 for char in value
    )


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


class Page(Input):
    page: int = Field(default=1, ge=1, le=10_000)
    per_page: int = Field(default=50, ge=1, le=100)


class InvoiceID(Input):
    invoice_id: ID


class ClientID(Input):
    client_id: ID


class ExpenseID(Input):
    expense_id: ID
