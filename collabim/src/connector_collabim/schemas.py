from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
IDList = Annotated[list[ID], Field(min_length=1, max_length=200)]
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Day = Annotated[str, Field(pattern=DATE_PATTERN, min_length=10, max_length=10)]
Text = Annotated[str, Field(min_length=1, max_length=128)]
# Position history is limited by the provider to 7000 keyword-days; one year keeps
# the request bounded and still covers every documented reporting horizon.
MAX_INTERVAL = timedelta(days=366)


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def interval(start: str, end: str) -> tuple[str, str]:
    """Validate a calendar-day interval `from`..`to` (inclusive, bounded)."""
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


# Provider pagination is `page` (1-based) + `itemsPerPage`; invalid values fail, never clamp.
class Page(Input):
    page: int = Field(default=1, ge=1, le=10_000, description="Číslo stránky (od 1)")
    items_per_page: int = Field(default=20, ge=1, le=100, description="Počet záznamů na stránku")


class ProjectList(Page):
    name_like: Text | None = Field(default=None, description="Část názvu projektu (nameLike)")
    active: bool | None = Field(
        default=None, description="Jen aktivní (true) nebo archivované (false) projekty"
    )


class ProjectID(Input):
    project_id: ID = Field(description="ID projektu")


class KeywordList(Page):
    project_id: ID = Field(description="ID projektu")
    keyword_like: Text | None = Field(
        default=None, description="Část textu klíčového slova (keywordLike)"
    )
    tags: Text | None = Field(default=None, description="Štítek klíčových slov (tags)")
    starred: bool | None = Field(default=None, description="Jen s hvězdičkou (true) / bez (false)")


class Window(Input):
    project_id: ID = Field(description="ID projektu")
    date_from: Day = Field(description="Začátek intervalu (YYYY-MM-DD)")
    date_to: Day = Field(description="Konec intervalu (YYYY-MM-DD), nejvýše rok po začátku")

    @model_validator(mode="after")
    def bounded(self) -> Window:
        interval(self.date_from, self.date_to)
        return self


class KeywordPositions(Window):
    project_keyword_ids: IDList | None = Field(
        default=None, description="ID klíčových slov projektu (projectKeywordIds)"
    )
    tags: Text | None = Field(default=None, description="Štítek klíčových slov (tags)")
    get_x_days: int | None = Field(
        default=None, ge=1, le=366, description="Vrátit jen posledních X dnů intervalu (getXDays)"
    )

    @model_validator(mode="after")
    def filtered(self) -> KeywordPositions:
        if self.project_keyword_ids is None and self.tags is None:
            raise ValueError("Zadejte project_keyword_ids nebo tags.")
        return self


class AggregatedPositions(Window):
    tags: Text | None = Field(default=None, description="Štítek klíčových slov (tags)")
    get_x_days: int | None = Field(
        default=None, ge=1, le=366, description="Vrátit jen posledních X dnů intervalu (getXDays)"
    )


class PositionDistribution(Window):
    tag_name: Text | None = Field(default=None, description="Jen pro daný štítek (tagName)")
    get_x_days: int | None = Field(
        default=None, ge=1, le=366, description="Vrátit jen posledních X dnů intervalu (getXDays)"
    )


class MarketShare(Window):
    search_engine_id: ID = Field(description="ID vyhledávače (searchEngineId)")
    tag_name: Text | None = Field(default=None, description="Jen pro daný štítek (tagName)")
    get_x_days: int | None = Field(
        default=None, ge=1, le=366, description="Vrátit jen posledních X dnů intervalu (getXDays)"
    )


class ActivityList(Page):
    project_id: ID | None = Field(default=None, description="ID projektu")
    added_on_from: Day | None = Field(default=None, description="Vytvořeno od (YYYY-MM-DD)")
    added_on_to: Day | None = Field(default=None, description="Vytvořeno do (YYYY-MM-DD)")
    type_id: ID | None = Field(default=None, description="ID typu aktivity (typeId)")
    state_id: ID | None = Field(default=None, description="ID stavu aktivity (stateId)")

    @model_validator(mode="after")
    def bounded(self) -> ActivityList:
        if (self.added_on_from is None) != (self.added_on_to is None):
            raise ValueError("Interval vyžaduje added_on_from i added_on_to.")
        if self.added_on_from is not None and self.added_on_to is not None:
            interval(self.added_on_from, self.added_on_to)
        return self
