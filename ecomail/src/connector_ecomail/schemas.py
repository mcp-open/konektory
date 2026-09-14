from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]


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


EMAIL_PATTERN = r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
TEXT_PATTERN = r"^[^\x00-\x1f\x7f]{1,128}$"

Email = Annotated[str, Field(pattern=EMAIL_PATTERN)]
Date = Annotated[str, Field(pattern=DATE_PATTERN)]
Text = Annotated[str, Field(pattern=TEXT_PATTERN)]


class Empty(Input):
    pass


class ListID(Input):
    list_id: ID = Field(description="ID seznamu kontaktů")


class SubscriberList(Input):
    list_id: ID = Field(description="ID seznamu kontaktů")
    page: int = Field(default=1, ge=1, le=100_000, description="Číslo stránky (od 1)")
    per_page: int = Field(default=20, ge=1, le=500, description="Počet kontaktů na stránku")
    status: Literal["subscribed", "unsubscribed", "not_confirmed", "bounced", "complained"] = (
        Field(default="subscribed", description="Filtr stavu kontaktu v seznamu")
    )


class SubscriberDetail(Input):
    email: Email = Field(description="E-mailová adresa kontaktu")
    list_id: ID | None = Field(
        default=None, description="ID seznamu; bez něj se vrací globální detail kontaktu"
    )


class CampaignList(Input):
    per_page: int = Field(default=20, ge=1, le=200, description="Počet kampaní na stránku")
    sort_by: Literal["id", "sent_at", "changed_at", "title", "status"] = Field(
        default="id", description="Sloupec řazení"
    )
    sort_dir: Literal["asc", "desc"] = Field(default="desc", description="Směr řazení")
    campaign_id: ID | None = Field(default=None, description="Filtr podle ID kampaně")
    title: Text | None = Field(default=None, description="Filtr podle názvu kampaně")
    subject: Text | None = Field(default=None, description="Filtr podle předmětu")
    status: int | None = Field(default=None, ge=0, le=100, description="Filtr podle kódu stavu")
    date_from: Date | None = Field(default=None, description="Od data (YYYY-MM-DD)")
    date_to: Date | None = Field(default=None, description="Do data (YYYY-MM-DD)")


class CampaignStats(Input):
    campaign_id: ID = Field(description="ID kampaně")
    from_date: Date | None = Field(default=None, description="Od data (YYYY-MM-DD)")
    to_date: Date | None = Field(default=None, description="Do data (YYYY-MM-DD)")
