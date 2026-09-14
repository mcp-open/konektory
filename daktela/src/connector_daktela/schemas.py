from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Daktela "unique name": ASCII letters, digits and underscore only (docs: "Sending /
# Receiving data format"); ticket names are numeric, users/queues/contacts are slugs.
NAME_PATTERN = r"^[A-Za-z0-9_]{1,64}$"
Name = Annotated[str, Field(pattern=NAME_PATTERN, min_length=1, max_length=64)]
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
Search = Annotated[str, Field(min_length=1, max_length=128)]
Sort = Literal["asc", "desc"]


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


# Bounded pagination (Daktela `take`/`skip`); invalid values fail instead of clamping.
class Page(Input):
    take: int = Field(default=20, ge=1, le=100, description="Počet záznamů (take)")
    skip: int = Field(default=0, ge=0, le=10_000, description="Počet přeskočených záznamů (skip)")


class TicketList(Page):
    stage: Literal["OPEN", "WAIT", "CLOSE", "ARCHIVE"] | None = Field(
        default=None, description="Fáze tiketu"
    )
    priority: Literal["LOW", "MEDIUM", "HIGH"] | None = Field(
        default=None, description="Priorita tiketu"
    )
    category: Name | None = Field(default=None, description="Unikátní jméno kategorie")
    user: Name | None = Field(default=None, description="Unikátní jméno přiřazeného uživatele")
    contact: Name | None = Field(default=None, description="Unikátní jméno kontaktu")
    title_contains: Search | None = Field(default=None, description="Část názvu tiketu")
    created_from: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Vytvořeno od (YYYY-MM-DD)"
    )
    created_to: str | None = Field(
        default=None, pattern=DATE_PATTERN, description="Vytvořeno do (YYYY-MM-DD)"
    )
    sort: Sort = Field(default="desc", description="Řazení podle data vytvoření")

    @model_validator(mode="after")
    def dates(self) -> TicketList:
        for value in (self.created_from, self.created_to):
            if value is not None:
                date.fromisoformat(value)
        return self


class TicketName(Input):
    name: Name = Field(description="Unikátní jméno (číslo) tiketu")


class ActivityList(Page):
    ticket: Name | None = Field(default=None, description="Unikátní jméno tiketu")
    type: (
        Literal["CALL", "EMAIL", "CHAT", "SMS", "FBM", "IGDM", "WAP", "VBR", "CUSTOM", "COMMENT"]
        | None
    ) = Field(default=None, description="Typ aktivity")
    action: Literal["WAIT", "OPEN", "POSTPONE", "CLOSE"] | None = Field(
        default=None, description="Stav aktivity"
    )
    queue: Name | None = Field(default=None, description="Unikátní jméno fronty")
    user: Name | None = Field(default=None, description="Unikátní jméno uživatele")
    sort: Sort = Field(default="desc", description="Řazení podle času")


class ContactList(Page):
    lastname_contains: Search | None = Field(default=None, description="Část příjmení")
    account: Name | None = Field(default=None, description="Unikátní jméno účtu (firmy)")


class ContactName(Input):
    name: Name = Field(description="Unikátní jméno kontaktu")


class QueueList(Page):
    type: (
        Literal[
            "in",
            "out",
            "ctc",
            "outbounder",
            "progressive",
            "dialer",
            "email",
            "sms",
            "chat",
            "fbm",
            "igdm",
            "socialmedia",
            "wap",
            "vbr",
            "custom",
        ]
        | None
    ) = Field(default=None, description="Typ fronty")


class UserList(Page):
    pass
