from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Guid = Annotated[
    str,
    Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
]
Code = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")]
# ISO 8601 with explicit offset, as documented for creationTimeFrom (e.g. 2017-12-12T22:08:01+0100).
Timestamp = Annotated[
    str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})$")
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


class OrderList(Input):
    page: int = Field(default=1, ge=1, le=10_000)
    items_per_page: int = Field(default=50, ge=1, le=50)
    status_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    creation_time_from: Timestamp | None = None
    creation_time_to: Timestamp | None = None
    change_time_from: Timestamp | None = None
    change_time_to: Timestamp | None = None


class OrderCode(Input):
    code: Code


class ProductList(Input):
    page: int = Field(default=1, ge=1, le=10_000)
    items_per_page: int = Field(default=20, ge=1, le=100)
    product_type: Literal["product", "service", "bazar"] | None = None
    category_guid: Guid | None = None
    change_time_from: Timestamp | None = None
    include_images: bool = False


class ProductGuid(Input):
    guid: Guid


class ProductCode(Input):
    code: Code


class CustomerList(Input):
    page: int = Field(default=1, ge=1, le=10_000)
    items_per_page: int = Field(default=20, ge=1, le=100)


class CustomerGuid(Input):
    guid: Guid


class Empty(Input):
    pass
