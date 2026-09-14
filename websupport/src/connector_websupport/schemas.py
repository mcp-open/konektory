from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]


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


# v1 seznamy: page/pagesize (výchozí velikost stránky je u providera neomezená).
class PageV1(Input):
    page: int = Field(default=1, ge=1, le=10_000)
    pagesize: int = Field(default=50, ge=1, le=200)


class ServiceID(Input):
    service_id: ID


# v2 seznamy: page/rowsPerPage.
class ServicePage(Input):
    service_id: ID
    page: int = Field(default=1, ge=1, le=10_000)
    rows_per_page: int = Field(default=50, ge=1, le=200)


class DnsRecordList(ServicePage):
    name: str | None = Field(default=None, min_length=1, max_length=253)
    content: str | None = Field(default=None, min_length=1, max_length=253)


class FtpAccountID(Input):
    service_id: ID
    ftp_account_id: ID
