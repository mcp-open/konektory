from __future__ import annotations

import re
from datetime import date
from typing import Any

from openmcp_connector_runtime import ConnectorError, ErrorCode
from pydantic import BaseModel, ConfigDict, model_validator


def validate_date_range(start: str | None, end: str | None) -> None:
    try:
        for value in (start, end):
            if value is not None:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
        if start and end and start > end:
            raise ValueError
    except ValueError as exc:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatné datum nebo rozsah dat.") from exc


def validate_page(page: int) -> None:
    if type(page) is not int or not 1 <= page <= 10_000:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatné číslo stránky.")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_max_length=256)

    @model_validator(mode="before")
    @classmethod
    def bounded_filters(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            raise ValueError("Argumenty musí být objekt.")
        for name, value in values.items():
            if isinstance(value, str):
                if not value or len(value) > 256 or any(ord(char) < 32 for char in value):
                    raise ValueError("Neplatný textový filtr.")
                if "time_from" in name or "time_to" in name:
                    validate_date_range(value, None)
            if (
                (name == "id" or name.endswith("_id"))
                and value is not None
                and (type(value) is not int or not 1 <= value <= 2**53 - 1)
            ):
                raise ValueError("Neplatné ID.")
        order_by = values.get("order_by", "creation_time")
        if not isinstance(order_by, str) or order_by not in {"creation_time", "last_update_time"}:
            raise ValueError("Neplatné řazení.")
        order_dir = values.get("order_dir", "desc")
        if not isinstance(order_dir, str) or order_dir not in {"asc", "desc"}:
            raise ValueError("Neplatný směr řazení.")
        return values
