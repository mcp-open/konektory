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


# Documented grammars: ``select`` is a comma-separated list of property names,
# ``sort`` the same with an optional leading ``-`` for descending order.
SELECT_PATTERN = r"^[a-z][a-z0-9_]{0,63}(,[a-z][a-z0-9_]{0,63})*$"
SELECT_MAX_LENGTH = 30 * 65 - 1  # 30 property names of up to 64 characters
SORT_PATTERN = r"^-?[a-z][a-z0-9_]{0,63}(,-?[a-z][a-z0-9_]{0,63}){0,9}$"
EMAIL_PATTERN = r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}$"
TEXT_PATTERN = r"^[^\x00-\x1f\x7f]{1,128}$"
_EMAIL_FIELD = r"(id|name|title|htmlbody|textbody|created)"
EMAIL_SELECT_PATTERN = rf"^{_EMAIL_FIELD}(,{_EMAIL_FIELD}){{0,5}}$"

Select = Annotated[str, Field(pattern=SELECT_PATTERN, max_length=SELECT_MAX_LENGTH)]
Sort = Annotated[str, Field(pattern=SORT_PATTERN)]
Text = Annotated[str, Field(pattern=TEXT_PATTERN)]


# Bounded offset pagination; invalid values fail instead of clamping.
class Page(Input):
    limit: int = Field(default=100, ge=1, le=500, description="Počet záznamů (max. 500)")
    offset: int = Field(default=0, ge=0, le=1_000_000, description="Přeskočené záznamy")


class ContactList(Page):
    select: Select | None = Field(default=None, description="Vybraná pole oddělená čárkou")
    sort: Sort | None = Field(default=None, description="Řazení, `-` před polem = sestupně")
    expand: Literal["customfields"] | None = Field(
        default=None, description="Rozbalit vnořené zdroje"
    )
    emailaddress: str | None = Field(default=None, pattern=EMAIL_PATTERN, description="E-mail")
    name: Text | None = Field(default=None, description="Filtr jména")
    surname: Text | None = Field(default=None, description="Filtr příjmení")
    company: Text | None = Field(default=None, description="Filtr firmy")
    country: Text | None = Field(default=None, description="Filtr země")
    town: Text | None = Field(default=None, description="Filtr města")
    language: str | None = Field(
        default=None, pattern=r"^[a-z]{2}_[A-Z]{2}$", description="Jazyk (POSIX, např. cs_CZ)"
    )
    blacklisted: Literal[0, 1] | None = Field(default=None, description="Stav blacklistu 0/1")


class ContactDetail(Input):
    contact: str = Field(
        pattern=r"^(?:[0-9]{1,18}|[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190})$",
        description="ID kontaktu nebo e-mailová adresa",
    )
    select: Select | None = Field(default=None, description="Vybraná pole oddělená čárkou")
    expand: Literal["customfields"] | None = Field(
        default=None, description="Rozbalit vnořené zdroje"
    )


class ContactlistList(Page):
    select: Select | None = Field(default=None, description="Vybraná pole oddělená čárkou")
    sort: Sort | None = Field(default=None, description="Řazení, `-` před polem = sestupně")


class ContactlistID(Input):
    contactlist_id: ID = Field(description="ID seznamu kontaktů")
    select: Select | None = Field(default=None, description="Vybraná pole oddělená čárkou")


class EmailList(Input):
    limit: int = Field(default=10, ge=1, le=10, description="Počet záznamů (max. 10)")
    offset: int = Field(default=0, ge=0, le=1_000_000, description="Přeskočené záznamy")
    select: str = Field(
        default="id,name,title,created",
        pattern=EMAIL_SELECT_PATTERN,
        description="Vybraná pole: id, name, title, htmlbody, textbody, created",
    )
    sort: str | None = Field(
        default=None,
        pattern=r"^-?(id|name|title)(,-?(id|name|title)){0,2}$",
        description="Řazení podle id, name, title; `-` = sestupně",
    )


class NewsletterList(Page):
    newsletter_id: ID | None = Field(default=None, description="Filtr podle ID newsletteru")
    email_id: ID | None = Field(default=None, description="Filtr podle ID e-mailu (šablony)")


class NewsletterStats(Page):
    select: Select | None = Field(default=None, description="Vybraná pole oddělená čárkou")


class CustomfieldList(Page):
    select: Select | None = Field(default=None, description="Vybraná pole oddělená čárkou")
    sort: Sort | None = Field(default=None, description="Řazení, `-` před polem = sestupně")
    expand: Literal["customfield_options"] | None = Field(
        default=None, description="Rozbalit možnosti výběrových polí"
    )
