from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Resource = Literal[
    "company", "person", "lead", "businessCase", "offer", "salesOrder", "project", "product"
]
Codebook = Literal[
    "companyCategory",
    "personCategory",
    "businessCaseCategory",
    "businessCasePhase",
    "businessCaseType",
    "leadCategory",
    "leadPhase",
    "currency",
    "taxRate",
    "productCategory",
    "productLine",
    "offerCategory",
    "offerStatus",
    "salesOrderCategory",
    "salesOrderStatus",
    "projectStatus",
]
ID = Annotated[int, Field(ge=1, le=2**53 - 1)]
ExternalID = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")
]
COMMON = {"fulltext", "sortColumn", "sortDirection"}
FILTERS = {
    "company": COMMON | {"name", "regNumber", "role", "state", "category", "owner", "id"},
    "person": COMMON
    | {"firstName", "lastName", "category", "owner", "primaryRelationship-company-id", "id"},
    "lead": COMMON | {"companyName", "lastName", "status", "owner", "id"},
    "businessCase": COMMON | {"name", "company", "owner", "status", "businessCasePhase", "id"},
    "offer": COMMON | {"code", "name", "company", "owner", "status", "id"},
    "salesOrder": COMMON | {"code", "name", "company", "owner", "status", "id"},
    "project": COMMON | {"code", "name", "company", "owner", "status", "id"},
    "product": COMMON | {"code", "name", "productCategory", "productLine", "id"},
}
INTEGER_FILTERS = {
    "id",
    "owner",
    "company",
    "category",
    "businessCasePhase",
    "productCategory",
    "productLine",
    "primaryRelationship-company-id",
}
SORT_COLUMNS = {"id", "name", "rowInfo.createdAt", "rowInfo.updatedAt"}


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


class Page(Input):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)


class Search(Page):
    fulltext: str | None = None


class CompanySearch(Search):
    name: str | None = None
    regNumber: str | None = None
    role: str | None = None
    state: str | None = None
    category: ID | None = None
    owner: ID | None = None
    sortColumn: Literal["id", "name", "rowInfo.createdAt", "rowInfo.updatedAt"] | None = None
    sortDirection: Literal["ASC", "DESC"] | None = None


class PersonSearch(Search):
    firstName: str | None = None
    lastName: str | None = None
    category: ID | None = None
    owner: ID | None = None
    company_id: ID | None = None


class BusinessCaseSearch(Search):
    name: str | None = None
    company: ID | None = None
    owner: ID | None = None
    status: str | None = None
    businessCasePhase: ID | None = None


class LeadSearch(Search):
    companyName: str | None = None
    lastName: str | None = None
    status: str | None = None
    owner: ID | None = None


class CompanyID(Input):
    company_id: ID


class PersonID(Input):
    person_id: ID


class BusinessCaseID(Input):
    business_case_id: ID


class LeadID(Input):
    lead_id: ID


class ExtID(Input):
    ext_id: ExternalID


class GenericList(Page):
    resource: Resource
    filters: dict[str, str | int | bool] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def allowed_filters(self) -> GenericList:
        for key, value in (self.filters or {}).items():
            if key not in FILTERS[self.resource]:
                raise ValueError("Nepovolený filtr zdroje.")
            if key in INTEGER_FILTERS:
                if type(value) is not int or not 1 <= value <= 2**53 - 1:
                    raise ValueError("Filtr vyžaduje bezpečné celočíselné ID.")
            elif not isinstance(value, str) or not valid_text(value):
                raise ValueError("Neplatný textový filtr.")
            if key == "sortColumn" and value not in SORT_COLUMNS:
                raise ValueError("Nepovolený sloupec řazení.")
            if key == "sortDirection" and value not in {"ASC", "DESC"}:
                raise ValueError("Nepovolený směr řazení.")
        return self


class GenericGet(Input):
    resource: Resource
    record_id: ID | ExternalID


class GenericExt(ExtID):
    resource: Resource


class CodebookList(Page):
    name: Codebook
    limit: int = Field(default=100, ge=1, le=100)
