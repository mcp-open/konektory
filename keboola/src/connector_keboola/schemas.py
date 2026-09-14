from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Storage identifiers such as ``in.c-main.orders`` or ``keboola.ex-db-mysql``.
StorageID = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")
]
Column = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
]


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
        if any(isinstance(value, str) and not valid_text(value) for value in values.values()):
            raise ValueError("Neplatný textový argument.")
        return values


class BucketList(Input):
    include_metadata: bool = False


class TableList(Input):
    bucket_id: StorageID | None = None
    include_columns: bool = False


class TableID(Input):
    table_id: StorageID


class TablePreview(Input):
    table_id: StorageID
    limit: int = Field(default=20, ge=1, le=100)
    columns: list[Column] | None = Field(default=None, min_length=1, max_length=50)


class ComponentList(Input):
    component_type: (
        Literal["extractor", "writer", "transformation", "application", "processor", "other"]
        | None
    ) = None


class ComponentID(Input):
    component_id: StorageID


class JobList(Input):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)
