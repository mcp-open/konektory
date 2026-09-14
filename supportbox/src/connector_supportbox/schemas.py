from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
# ISO date or date-time accepted by the provider's date-time filters.
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$"
DateTime = Annotated[str, Field(pattern=DATE_PATTERN, min_length=10, max_length=19)]
Status = Literal["new", "pending", "resolved", "spam"]


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

    @model_validator(mode="after")
    def iso_dates(self) -> Input:
        for name, value in self.__dict__.items():
            if name.endswith(("_from", "_to")) and isinstance(value, str):
                datetime.fromisoformat(value)
        return self


# Bounded pagination (provider `page`/`per_page`, max 50); invalid values fail, never clamp.
class Page(Input):
    page: int = Field(default=1, ge=1, le=10_000, description="Číslo stránky (od 1)")
    per_page: int = Field(default=25, ge=1, le=50, description="Počet záznamů na stránku")


class MailTicketList(Page):
    status: Status | None = Field(default=None, description="Stav tiketu")
    mailbox_id: ID | None = Field(default=None, description="ID schránky")
    assigned_user_id: ID | None = Field(default=None, description="ID přiřazeného uživatele")
    unassigned: bool = Field(default=False, description="Jen tikety bez přiřazeného uživatele")
    tag_id: ID | None = Field(default=None, description="ID štítku")
    created_from: DateTime | None = Field(default=None, description="Vytvořeno od (ISO 8601)")
    created_to: DateTime | None = Field(default=None, description="Vytvořeno do (ISO 8601)")
    last_message_from: DateTime | None = Field(
        default=None, description="Poslední zpráva od (ISO 8601)"
    )
    last_message_to: DateTime | None = Field(
        default=None, description="Poslední zpráva do (ISO 8601)"
    )

    @model_validator(mode="after")
    def assignment(self) -> MailTicketList:
        if self.unassigned and self.assigned_user_id is not None:
            raise ValueError("Nelze kombinovat unassigned a assigned_user_id.")
        return self


class MailTicketID(Input):
    ticket_id: ID = Field(description="ID e-mailového tiketu")


class MailTicketMessages(Page):
    ticket_id: ID = Field(description="ID e-mailového tiketu")


class MailboxList(Page):
    pass


class UserList(Page):
    pass


class TagList(Page):
    pass
