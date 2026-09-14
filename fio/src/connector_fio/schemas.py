from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

DAY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Day = Annotated[str, Field(pattern=DAY_PATTERN, min_length=10, max_length=10)]
# Adapter bound: one request covers at most 90 days (Fio itself needs an extra
# internet-banking unlock for data older than 90 days).
MAX_PERIOD = timedelta(days=90)


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def period(date_from: str, date_to: str) -> tuple[date, date]:
    first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if last < first or last - first > MAX_PERIOD:
        raise ValueError("Období musí být neprázdné a nejvýše 90 dní dlouhé.")
    return first, last


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


class Period(Input):
    date_from: Day = Field(description="Začátek období (YYYY-MM-DD)")
    date_to: Day = Field(description="Konec období (YYYY-MM-DD), nejvýše 90 dní po začátku")

    @model_validator(mode="after")
    def bounded(self) -> Period:
        period(self.date_from, self.date_to)
        return self


class Statement(Input):
    year: int = Field(ge=2000, le=2100, description="Rok výpisu (rrrr)")
    statement_id: int = Field(ge=1, le=999_999, description="Číslo výpisu v daném roce")
