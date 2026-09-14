from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Slovenské IČO je osmimístné číslo (včetně vedoucích nul).
ICO = Annotated[str, Field(min_length=8, max_length=8, pattern=r"^[0-9]{8}$")]
Template = Literal[
    "Template2011v2",
    "Template2014",
    "Template2014micro",
    "TemplateFinancial",
    "TemplateROPO",
    "TemplateNujPU",
]


def valid_text(value: str) -> bool:
    return (
        bool(value) and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
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


class Company(Input):
    ico: ICO


class Autocomplete(Input):
    query: str = Field(min_length=2, max_length=100)


class Statement(Input):
    ico: ICO
    year: int = Field(ge=1993, le=2100)
    template: Template
