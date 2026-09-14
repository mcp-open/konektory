from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
Guid = Annotated[str, Field(pattern=GUID_PATTERN, min_length=36, max_length=36)]
GuidList = Annotated[list[Guid], Field(min_length=1, max_length=50)]
# ISO 8601 UTC instant (or a calendar day, expanded to midnight UTC).
INSTANT_PATTERN = r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$"
Instant = Annotated[str, Field(pattern=INSTANT_PATTERN, min_length=10, max_length=20)]
MAX_INTERVAL = timedelta(days=92)  # provider: "max length 3 months"
ReservationState = Literal[
    "Inquired", "Confirmed", "Started", "Processed", "Canceled", "Optional", "Requested"
]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def instant(value: str) -> str:
    """Normalise a validated Instant to the provider's `YYYY-MM-DDTHH:MM:SSZ` form."""
    normalized = value if len(value) == 20 else f"{value}T00:00:00Z"
    datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%SZ")
    return normalized


def interval(start: str, end: str) -> dict[str, str]:
    start_utc, end_utc = instant(start), instant(end)
    first = datetime.strptime(start_utc, "%Y-%m-%dT%H:%M:%SZ")
    last = datetime.strptime(end_utc, "%Y-%m-%dT%H:%M:%SZ")
    if last < first or last - first > MAX_INTERVAL:
        raise ValueError("Interval musí být neprázdný a nejvýše 3 měsíce dlouhý.")
    return {"StartUtc": start_utc, "EndUtc": end_utc}


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


# Cursor pagination (`Limitation: {Count, Cursor}`); invalid values fail, never clamp.
class Page(Input):
    count: int = Field(default=25, ge=1, le=100, description="Počet záznamů (Limitation.Count)")
    cursor: Guid | None = Field(
        default=None, description="Cursor z předchozí stránky (Limitation.Cursor)"
    )


class Window(Page):
    start: Instant = Field(description="Začátek intervalu UTC (YYYY-MM-DD nebo ...THH:MM:SSZ)")
    end: Instant = Field(description="Konec intervalu UTC, nejvýše 3 měsíce po začátku")

    @model_validator(mode="after")
    def bounded(self) -> Window:
        interval(self.start, self.end)
        return self


class ReservationList(Window):
    window: Literal["scheduled_start", "scheduled_end", "colliding", "created", "updated"] = (
        Field(default="scheduled_start", description="Který čas rezervace interval filtruje")
    )
    states: list[ReservationState] | None = Field(
        default=None, min_length=1, max_length=7, description="Stavy rezervací"
    )
    service_ids: GuidList | None = Field(default=None, description="ID služeb (Services)")


class CustomerList(Page):
    window: Literal["created", "updated"] = Field(
        default="updated", description="Který čas hosta interval filtruje"
    )
    start: Instant | None = Field(default=None, description="Začátek intervalu UTC")
    end: Instant | None = Field(default=None, description="Konec intervalu UTC (max. 3 měsíce)")
    customer_ids: GuidList | None = Field(default=None, description="ID hostů (Customers)")
    include_addresses: bool = Field(default=False, description="Přidat adresy hostů")

    @model_validator(mode="after")
    def filtered(self) -> CustomerList:
        if (self.start is None) != (self.end is None):
            raise ValueError("Interval vyžaduje start i end.")
        if self.start is None and self.customer_ids is None:
            raise ValueError("Zadejte interval nebo customer_ids.")
        if self.start is not None and self.end is not None:
            interval(self.start, self.end)
        return self


class ServiceList(Page):
    service_ids: GuidList | None = Field(default=None, description="ID služeb")
    service_type: Literal["Bookable", "Additional"] | None = Field(
        default=None, description="Typ služby"
    )


class ResourceList(Page):
    resource_ids: GuidList | None = Field(default=None, description="ID zdrojů (pokojů)")
    names: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        default=None, min_length=1, max_length=50, description="Názvy zdrojů (čísla pokojů)"
    )
    include_inactive: bool = Field(default=False, description="Zahrnout neaktivní zdroje")


class EnterpriseList(Page):
    enterprise_ids: GuidList | None = Field(default=None, description="ID podniků")


class Configuration(Input):
    enterprise_id: Guid | None = Field(
        default=None, description="ID podniku (jen pro portfolio tokeny)"
    )
