from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
# Opaque signed cursor copied from the provider's ``pagination.next``; never a URL.
Cursor = Annotated[str | None, Field(default=None, min_length=1, max_length=1024)]
AnnotationStatus = Literal[
    "confirmed",
    "created",
    "deleted",
    "exported",
    "exporting",
    "failed_export",
    "failed_import",
    "importing",
    "in_workflow",
    "postponed",
    "purged",
    "rejected",
    "reviewing",
    "split",
    "to_review",
]


def valid_text(value: str, limit: int = 256) -> bool:
    return (
        bool(value)
        and len(value) <= limit
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


# Closed argument object: unknown keys, raw URLs, control characters and
# type coercion are rejected before any handler runs. Keep models docstring-free;
# a docstring would change the JSON schema published in connector.yaml.
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_max_length=1024)

    @model_validator(mode="before")
    @classmethod
    def text_values(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            raise ValueError("Argumenty musí být objekt.")
        for key, value in values.items():
            limit = 1024 if key == "cursor" else 256
            if isinstance(value, str) and not valid_text(value, limit):
                raise ValueError("Neplatný textový argument.")
        return values


# Provider pagination is cursor based; ``page_size`` is capped at 100 by the API.
class Page(Input):
    page_size: int = Field(default=20, ge=1, le=100)
    cursor: Cursor


class WorkspaceList(Page):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    ordering: Literal["id", "-id", "name", "-name"] = "id"


class QueueList(Page):
    workspace_id: ID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    ordering: Literal["id", "-id", "name", "-name", "workspace", "-workspace"] = "id"


class QueueID(Input):
    queue_id: ID


class AnnotationList(Page):
    queue_id: ID | None = None
    status: list[AnnotationStatus] | None = Field(default=None, min_length=1, max_length=15)
    search: str | None = Field(default=None, min_length=1, max_length=128)
    ordering: Literal[
        "created_at",
        "-created_at",
        "modified_at",
        "-modified_at",
        "confirmed_at",
        "-confirmed_at",
        "exported_at",
        "-exported_at",
        "status",
        "-status",
        "queue",
        "-queue",
    ] = "-created_at"


class AnnotationID(Input):
    annotation_id: ID


class DocumentList(Page):
    original_file_name: str | None = Field(default=None, min_length=1, max_length=128)
    ordering: Literal[
        "id",
        "-id",
        "arrived_at",
        "-arrived_at",
        "created_at",
        "-created_at",
        "original_file_name",
        "-original_file_name",
    ] = "-arrived_at"
