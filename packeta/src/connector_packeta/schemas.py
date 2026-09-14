from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Packeta packet ID: "Z1234567890" nebo čistě číselný tvar.
PacketID = Annotated[str, Field(min_length=6, max_length=17, pattern=r"^Z?[0-9]{6,16}$")]
# Svozový list (D-kód / B-kód), např. "D-123-XM-12345678".
ShipmentID = Annotated[str, Field(min_length=5, max_length=40, pattern=r"^[A-Z0-9*-]{5,40}$")]
Language = Literal["cs", "sk", "en", "hu", "pl", "ro", "de"]


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


class Packet(Input):
    packet_id: PacketID


class Shipment(Input):
    shipment_id: ShipmentID


class CarrierList(Input):
    lang: Language = "cs"
    country: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    name: str | None = Field(default=None, min_length=1, max_length=64)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10_000)
