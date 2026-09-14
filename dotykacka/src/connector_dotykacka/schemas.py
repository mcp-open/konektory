from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListOrdersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: str | None = None
    date_to: str | None = None
    include_items: bool = False
    limit: int = 50
    page: int = 1

    @field_validator("limit")
    @classmethod
    def clamp_limit(cls, value: int) -> int:
        return 50 if value <= 0 else min(value, 100)

    @field_validator("page")
    @classmethod
    def validate_page(cls, value: int) -> int:
        if value < 1:
            raise ValueError("page must be positive")
        return min(value, 10_000)


class SalesSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: str | None = None
    date_to: str | None = None

