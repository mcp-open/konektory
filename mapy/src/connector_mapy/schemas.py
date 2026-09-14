from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Lon = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Lat = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Language = Literal[
    "cs", "de", "el", "en", "es", "fr", "it", "nl", "pl", "pt", "ru", "sk", "tr", "uk"
]
EntityType = Literal[
    "regional",
    "regional.country",
    "regional.region",
    "regional.municipality",
    "regional.municipality_part",
    "regional.street",
    "regional.address",
    "poi",
    "coordinate",
]
RouteType = Literal[
    "car_fast",
    "car_fast_traffic",
    "car_short",
    "foot_fast",
    "foot_hiking",
    "bike_road",
    "bike_mountain",
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


class Position(Input):
    lon: Lon
    lat: Lat


class Query(Input):
    query: str = Field(min_length=1, max_length=150)
    lang: Language = "cs"
    limit: int = Field(default=5, ge=1, le=15)
    entity_type: EntityType | None = None
    locality: str | None = Field(default=None, min_length=1, max_length=128)


class ReverseGeocode(Input):
    lon: Lon
    lat: Lat
    lang: Language = "cs"


class Route(Input):
    start: Position
    end: Position
    route_type: RouteType = "car_fast"
    lang: Language = "cs"
    avoid_toll: bool = False
    avoid_highways: bool = False
    waypoints: list[Position] = Field(default_factory=list, max_length=15)


class Elevation(Input):
    positions: list[Position] = Field(min_length=1, max_length=64)
    lang: Language = "cs"
