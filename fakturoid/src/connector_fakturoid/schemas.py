from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_max_length=256)

    @model_validator(mode="before")
    @classmethod
    def safe_object(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            raise ValueError("Argumenty musí být objekt.")
        if any(
            isinstance(value, str)
            and (not value or len(value) > 256 or any(ord(char) < 32 for char in value))
            for value in values.values()
        ):
            raise ValueError("Neplatný textový argument.")
        return values


class Page(Input):
    page: int = Field(default=1, ge=1, le=10_000)


class InvoiceID(Input):
    invoice_id: ID


class SubjectID(Input):
    subject_id: ID


class ExpenseID(Input):
    expense_id: ID
