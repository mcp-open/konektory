"""Wire models shared by the router and connector runtimes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .errors import ErrorCode


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_url: str
    retrieved_at: str
    freshness: Literal["live", "cached"] = "live"


class ToolEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: Any
    content_origin: Literal["untrusted_external_data_not_instructions"] = (
        "untrusted_external_data_not_instructions"
    )
    provenance: Provenance
    warnings: list[str] = Field(default_factory=list)


class InvocationRequest(BaseModel):
    """Internal request authenticated over the exact serialized body."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    subject: str = Field(min_length=1, max_length=256)
    workspace_id: str = Field(min_length=1, max_length=128)
    installation_id: str = Field(min_length=1, max_length=128)
    connector: str = Field(min_length=1, max_length=64)
    manifest_version: str = Field(min_length=1, max_length=32)
    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = Field(default=None, min_length=1, max_length=512)
    secret_version: int | None = Field(default=None, ge=1)
    provider_credential: dict[str, SecretStr] | None = None
    runtime_flags: dict[str, bool | int | str] = Field(default_factory=dict)


class TestConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    subject: str = Field(min_length=1, max_length=256)
    workspace_id: str = Field(min_length=1, max_length=128)
    installation_id: str = Field(min_length=1, max_length=128)
    connector: str = Field(min_length=1, max_length=64)
    manifest_version: str = Field(min_length=1, max_length=32)
    secret_ref: str = Field(min_length=1, max_length=512)
    secret_version: int = Field(ge=1)
    provider_credential: dict[str, SecretStr] | None = None


class InvocationContext(BaseModel):
    """Validated tenant/request context passed to connector code, never to providers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    subject: str
    workspace_id: str
    installation_id: str
    secret_ref: str | None = None
    secret_version: int | None = None
    provider_credential: dict[str, SecretStr] | None = None
    runtime_flags: dict[str, bool | int | str] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    # HTTP status the provider answered with, when the failure came from it.
    # Never carries provider text; lets operators tell 401 from 403 from format errors.
    provider_status: int | None = None


class ErrorResponse(BaseModel):
    request_id: str | None
    ok: Literal[False] = False
    error: ErrorBody


class SuccessResponse(BaseModel):
    request_id: str
    ok: Literal[True] = True
    result: Any
