from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
# Transaction code as documented (``AAAA-BBBB-CCCC``); letters, digits and dashes only.
TRANS_ID_PATTERN = r"^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$"
# Documented enums of ``GET /v2.0/method.json`` query parameters.
Currency = Literal["CZK", "EUR", "PLN", "HUF", "USD", "GBP", "RON", "NOK", "SEK"]
Country = Literal[
    "AT", "BE", "CY", "CZ", "DE", "EE", "EL", "ES", "FI", "FR", "GB", "HR", "HU", "IE",
    "IT", "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SL", "SK", "SE", "US",
]  # fmt: skip
Lang = Literal[
    "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "hr", "hu", "it", "lt",
    "lv", "nl", "no", "pl", "pt", "ro", "ru", "sl", "sk", "sv", "uk", "vi",
]  # fmt: skip


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


class TransID(Input):
    trans_id: str = Field(min_length=14, max_length=14, pattern=TRANS_ID_PATTERN)


class TransferList(Input):
    date: str = Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    test: bool = False

    @field_validator("date")
    @classmethod
    def calendar_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class TransferID(Input):
    transfer_id: ID
    test: bool = False


class MethodList(Input):
    lang: Lang | None = None
    currency: Currency | None = None
    country: Country | None = None
