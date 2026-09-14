from __future__ import annotations

from typing import Annotated, Any

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
        for value in values.values():
            items = value if isinstance(value, list) else [value]
            if any(isinstance(item, str) and not valid_text(item) for item in items):
                raise ValueError("Neplatný textový argument.")
        return values


# Documented value grammars: ``field:value`` filters, ``field:asc|desc`` sort,
# ``type:count`` result-type specs and comma-separated field lists.
FILTER_PATTERN = r"^[A-Za-z0-9_.-]{1,64}:[^\x00-\x1f\x7f]{1,128}$"
SORT_PATTERN = r"^[A-Za-z0-9_.-]{1,64}:(asc|desc)$"
_TYPE_COUNT = r"[a-z][a-z0-9_]{0,31}:(?:[1-9]|[1-4][0-9]|50)"
TYPE_SPEC_PATTERN = rf"^{_TYPE_COUNT}(,{_TYPE_COUNT}){{0,4}}$"
FIELDS_PATTERN = r"^[A-Za-z0-9_.-]{1,64}(,[A-Za-z0-9_.-]{1,64})*$"
FIELDS_MAX_LENGTH = 50 * 65 - 1  # 50 names of up to 64 characters, comma separated
TYPES_PATTERN = r"^[a-z][a-z0-9_]{0,31}(,[a-z][a-z0-9_]{0,31}){0,9}$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"
ITEM_ID_PATTERN = r"^[^\x00-\x1f\x7f\s]{1,256}$"

Filter = Annotated[str, Field(pattern=FILTER_PATTERN)]
Fields = Annotated[str, Field(pattern=FIELDS_PATTERN, max_length=FIELDS_MAX_LENGTH)]
TypeSpec = Annotated[str, Field(pattern=TYPE_SPEC_PATTERN)]


class Search(Input):
    q: str | None = Field(default=None, min_length=1, max_length=256, description="Hledaný dotaz")
    size: int = Field(default=10, ge=1, le=200, description="Počet výsledků (max. 200)")
    page: int = Field(default=1, ge=1, le=1000, description="Číslo stránky výsledků")
    filters: list[Filter] | None = Field(
        default=None, max_length=20, description="Filtry `pole:hodnota` (parametr f[])"
    )
    must_filters: list[Filter] | None = Field(
        default=None, max_length=20, description="Striktní AND filtry (parametr f_must[])"
    )
    sort: str | None = Field(
        default=None, pattern=SORT_PATTERN, description="Řazení `pole:asc|desc`"
    )
    facets: Fields | None = Field(default=None, description="Facety oddělené čárkou")
    quicksearch_types: Fields | None = Field(
        default=None, description="Sekundární typy obsahu oddělené čárkou"
    )
    hit_fields: Fields | None = Field(default=None, description="Vrácená pole oddělená čárkou")
    use_fixits: bool = Field(default=True, description="Použít opravy překlepů a přesměrování")

    @model_validator(mode="after")
    def query_or_filters(self) -> Search:
        if self.q is None and not self.filters and not self.must_filters:
            raise ValueError("Zadejte dotaz q nebo alespoň jeden filtr.")
        return self


class Autocomplete(Input):
    q: str = Field(min_length=1, max_length=256, description="Částečný dotaz uživatele")
    type: TypeSpec = Field(default="item:6", description="Typy a počty výsledků `typ:počet`")
    hit_fields: Fields | None = Field(default=None, description="Vrácená pole oddělená čárkou")


class TopItems(Input):
    type: TypeSpec = Field(default="item:10", description="Typy a počty výsledků `typ:počet`")
    hit_fields: Fields | None = Field(default=None, description="Vrácená pole oddělená čárkou")


class TrendingQueries(Input):
    pass


class Recommend(Input):
    recommendation_type: str = Field(
        pattern=r"^[a-z0-9_]{1,64}$", description="Typ doporučení (např. bestsellers)"
    )
    item_ids: list[Annotated[str, Field(pattern=ITEM_ID_PATTERN)]] | None = Field(
        default=None, min_length=1, max_length=10, description="Identity položek (max. 10)"
    )
    size: int = Field(default=10, ge=1, le=50, description="Počet doporučení (max. 50)")
    hit_fields: list[Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]] | None = Field(
        default=None, min_length=1, max_length=50, description="Vrácená pole"
    )
    recommender_client_identifier: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN, description="Identifikátor umístění widgetu"
    )


class ContentExport(Input):
    size: int = Field(default=100, ge=1, le=500, description="Počet záznamů (max. 500)")
    hit_fields: Fields | None = Field(default=None, description="Exportovaná pole oddělená čárkou")
    requested_types: str | None = Field(
        default=None, pattern=TYPES_PATTERN, description="Typy objektů oddělené čárkou"
    )
