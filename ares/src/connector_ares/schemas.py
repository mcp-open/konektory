from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ico: str


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obchodni_jmeno: str
    adresa: str | None = None
    start: int = Field(default=0, ge=0)
    pocet: int = Field(default=10, ge=1, le=50)

    @field_validator("obchodni_jmeno")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("obchodni_jmeno must contain at least two characters")
        return value

    @field_validator("adresa")
    @classmethod
    def normalize_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class Sidlo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    textova_adresa: str = Field(alias="textovaAdresa", default="")
    nazev_obce: str = Field(alias="nazevObce", default="")
    psc: int | None = None


class SubjektData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ico: str
    obchodni_jmeno: str = Field(alias="obchodniJmeno")
    pravni_forma: str = Field(alias="pravniForma", default="")
    datum_vzniku: str | None = Field(alias="datumVzniku", default=None)
    dic: str | None = None
    sidlo: Sidlo | None = None
    cz_nace: list[str] = Field(alias="czNace", default_factory=list)
    registrace: list[str] = Field(default_factory=list)


class SubjektSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ico: str | None = None
    obchodni_jmeno: str = Field(alias="obchodniJmeno")
    pravni_forma: str = Field(alias="pravniForma", default="")
    sidlo: Sidlo | None = None


class SubjektSeznamData(BaseModel):
    pocet_celkem: int
    start: int
    pocet: int
    subjekty: list[SubjektSummary]

