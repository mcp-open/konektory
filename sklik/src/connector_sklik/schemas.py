from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
IDList = Annotated[list[ID], Field(min_length=1, max_length=100)]
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Day = Annotated[str, Field(pattern=DATE_PATTERN, min_length=10, max_length=10)]
Granularity = Literal["total", "daily", "weekly", "monthly", "quarterly", "yearly"]
# Statistics are bounded to one year; the provider additionally limits the report size
# (entities × periods, see api.limits.statsDataLimit) and rejects larger reports itself.
MAX_INTERVAL = timedelta(days=366)


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def interval(start: str, end: str) -> tuple[str, str]:
    """Validate a calendar-day interval `dateFrom`..`dateTo` (inclusive, bounded)."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first or last - first > MAX_INTERVAL:
        raise ValueError("Interval musí být neprázdný a nejvýše jeden rok dlouhý.")
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


# Provider `displayOptions: {offset, limit}`; invalid values fail instead of clamping.
class Page(Input):
    limit: int = Field(
        default=100, ge=1, le=500, description="Počet záznamů (displayOptions.limit)"
    )
    offset: int = Field(
        default=0, ge=0, le=100_000, description="Posun záznamů (displayOptions.offset)"
    )
    include_deleted: bool = Field(
        default=False, description="Zahrnout i smazané entity (jinak isDeleted=false)"
    )


class CampaignList(Page):
    campaign_ids: IDList | None = Field(default=None, description="Jen tyto kampaně (ids)")


class GroupList(Page):
    campaign_ids: IDList | None = Field(
        default=None, description="Sestavy z těchto kampaní (campaign.ids)"
    )
    group_ids: IDList | None = Field(default=None, description="Jen tyto sestavy (ids)")


class KeywordList(Page):
    campaign_ids: IDList | None = Field(
        default=None, description="Klíčová slova z těchto kampaní (campaign.ids)"
    )
    group_ids: IDList | None = Field(
        default=None, description="Klíčová slova z těchto sestav (group.ids)"
    )
    keyword_ids: IDList | None = Field(default=None, description="Jen tato klíčová slova (ids)")


class AdList(Page):
    campaign_ids: IDList | None = Field(
        default=None, description="Inzeráty z těchto kampaní (campaign.ids)"
    )
    group_ids: IDList | None = Field(
        default=None, description="Inzeráty z těchto sestav (group.ids)"
    )
    ad_ids: IDList | None = Field(default=None, description="Jen tyto inzeráty (ids)")


class CampaignStats(Input):
    date_from: Day = Field(description="Začátek období (YYYY-MM-DD)")
    date_to: Day = Field(description="Konec období (YYYY-MM-DD), nejvýše rok po začátku")
    campaign_ids: IDList | None = Field(default=None, description="Jen tyto kampaně (ids)")
    granularity: Granularity = Field(
        default="total", description="Členění statistik (displayOptions.statGranularity)"
    )
    include_deleted: bool = Field(default=False, description="Zahrnout i smazané kampaně")
    limit: int = Field(default=100, ge=1, le=500, description="Počet kampaní v reportu")
    offset: int = Field(default=0, ge=0, le=100_000, description="Posun kampaní v reportu")

    @model_validator(mode="after")
    def bounded(self) -> CampaignStats:
        interval(self.date_from, self.date_to)
        return self
