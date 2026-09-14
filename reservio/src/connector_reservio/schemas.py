from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
Guid = Annotated[str, Field(pattern=GUID_PATTERN, min_length=36, max_length=36)]
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Day = Annotated[str, Field(pattern=DATE_PATTERN, min_length=10, max_length=10)]
# Availability is generated per slot; a quarter keeps the response bounded.
MAX_INTERVAL = timedelta(days=92)
Sort = Literal["createdAt", "-createdAt"]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def interval(start: str, end: str) -> tuple[str, str]:
    """Validate a calendar-day interval `filter[from]`..`filter[to]` (bounded)."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first or last - first > MAX_INTERVAL:
        raise ValueError("Interval musí být neprázdný a nejvýše 3 měsíce dlouhý.")
    return first.isoformat(), last.isoformat()


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


class ServiceID(Input):
    service_id: Guid = Field(description="ID služby")


class Sorted(Input):
    sort: Sort = Field(
        default="-createdAt", description="Řazení podle vytvoření (sort; - = sestupně)"
    )


class Window(Input):
    date_from: Day = Field(description="Začátek intervalu (YYYY-MM-DD, filter[from])")
    date_to: Day = Field(description="Konec intervalu (YYYY-MM-DD, filter[to]), max. 3 měsíce")
    resource_id: Guid | None = Field(default=None, description="Jen pro zdroj (filter[resourceId])")

    @model_validator(mode="after")
    def bounded(self) -> Window:
        interval(self.date_from, self.date_to)
        return self


class BookingSlots(Window):
    service_id: Guid = Field(description="Služba pro generování slotů (filter[serviceId])")
