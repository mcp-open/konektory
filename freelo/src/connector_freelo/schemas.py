from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]


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


class ProjectList(Input):
    order_by: Literal["name", "date_add", "date_edited_at"] = "name"
    order: Literal["asc", "desc"] = "asc"


class ProjectID(Input):
    project_id: ID


class TaskList(Input):
    page: int = Field(default=1, ge=1, le=10_000)
    project_id: ID | None = None
    search: str | None = Field(default=None, min_length=1, max_length=128)


class TaskID(Input):
    task_id: ID
    comments_limit: int = Field(default=20, ge=0, le=100)
