from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Lang = Literal["cs", "sk", "pl", "hu", "ro", "gb", "us"]
Keyword = Annotated[str, Field(min_length=2, max_length=80)]
# Domain, subdomain or URL without whitespace; the provider caps the value at 253 chars.
Target = Annotated[
    str,
    Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9][A-Za-z0-9._~:/?#@!$&'()*+,;=%-]*$"),
]


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


class KeywordSearchVolume(Input):
    keyword: Keyword
    lang: Lang = "cs"


class KeywordSuggestions(Input):
    keyword: Keyword
    lang: Lang = "cs"
    suggestions_type: Literal["questions", "new", "trending"] | None = None
    with_keyword_data: bool = False


class WebsiteStats(Input):
    target: Target
    target_type: Literal["domain", "subdomain", "exact", "prefix"] = "domain"
    lang: Lang = "cs"
    scheme: Literal["https", "http"] | None = None


class WebsiteStatsRange(Input):
    target: Target
    target_type: Literal["domain", "subdomain", "exact", "prefix"] = "domain"
    lang: Lang = "cs"
    scheme: Literal["https", "http"] | None = None
    period: Literal["daily", "weekly", "monthly"] | None = None
