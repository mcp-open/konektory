"""Authenticated internal HTTP invocation runtime.

This is not a public MCP server. The Go MCP router performs protocol handling,
catalog and policy decisions, then calls this narrow internal boundary.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .auth import (
    InvocationBinding,
    InvocationTokenVerifier,
    TokenValidationError,
    extract_bearer,
    loads_json_no_duplicates,
)
from .errors import ConnectorError, ErrorCode
from .models import (
    ErrorBody,
    ErrorResponse,
    InvocationContext,
    InvocationRequest,
    SuccessResponse,
    TestConnectionRequest,
)
from .provider import privacy_mode
from .replay import ReplayStore, ReplayStoreUnavailable, replay_store_from_env
from .signing_secret import invocation_signing_key_from_env, validate_invocation_signing_key

logger = logging.getLogger("openmcp.connector")
_MAX_REQUEST_BYTES = 64 * 1024

ToolHandler = Callable[[BaseModel, InvocationContext], Awaitable[Any]]
TestHandler = Callable[[InvocationContext], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    handler: ToolHandler
    description: str
    read_only: bool = True


@dataclass(frozen=True)
class ConnectorDefinition:
    slug: str
    version: str
    tools: Mapping[str, ToolSpec]
    test_connection: TestHandler | None = None
    requires_secret: bool = False
    close: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)


def _status(code: ErrorCode) -> int:
    return {
        ErrorCode.INVALID_INPUT: 400,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.UNAUTHORIZED: 401,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.CREDENTIAL_INVALID: 424,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.UPSTREAM_UNAVAILABLE: 503,
        ErrorCode.UPSTREAM_ERROR: 502,
        ErrorCode.INTERNAL: 500,
    }[code]


def _error(request_id: str | None, exc: ConnectorError) -> JSONResponse:
    body = ErrorResponse(
        request_id=request_id,
        error=ErrorBody(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            provider_status=exc.provider_status,
        ),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=_status(exc.code))


async def _body(request: Request) -> tuple[Any, bytes]:
    length = request.headers.get("content-length")
    if length:
        try:
            parsed_length = int(length)
            if parsed_length < 0:
                raise ValueError("negative content length")
            if parsed_length > _MAX_REQUEST_BYTES:
                raise ConnectorError(ErrorCode.INVALID_INPUT, "Požadavek je příliš velký.")
        except ValueError as exc:
            raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatná délka požadavku.") from exc
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > _MAX_REQUEST_BYTES:
            raise ConnectorError(ErrorCode.INVALID_INPUT, "Požadavek je příliš velký.")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        return loads_json_no_duplicates(raw), raw
    except TokenValidationError as exc:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Požadavek není platný JSON.") from exc


def _context(request: InvocationRequest | TestConnectionRequest) -> InvocationContext:
    return InvocationContext(
        request_id=request.request_id,
        subject=request.subject,
        workspace_id=request.workspace_id,
        installation_id=request.installation_id,
        secret_ref=request.secret_ref,
        secret_version=request.secret_version,
        provider_credential=request.provider_credential,
        runtime_flags=getattr(request, "runtime_flags", {}),
    )


def create_app(
    definition: ConnectorDefinition,
    *,
    signing_key: str | None = None,
    replay_store: ReplayStore | None = None,
) -> Starlette:
    key = (
        validate_invocation_signing_key(signing_key)
        if signing_key is not None
        else invocation_signing_key_from_env()
    )
    store = replay_store if replay_store is not None else replay_store_from_env(definition.slug)
    verifier = InvocationTokenVerifier(
        key,
        connector=definition.slug,
        manifest_version=definition.version,
        replay_store=store,
    )

    def bearer(request: Request) -> str:
        try:
            return extract_bearer(request.scope.get("headers", []))
        except TokenValidationError as exc:
            raise ConnectorError(ErrorCode.UNAUTHORIZED, "Neplatná interní autorizace.") from exc

    async def authorize(
        token: str,
        parsed: InvocationRequest | TestConnectionRequest,
        tool: str,
        raw_body: bytes,
    ) -> None:
        try:
            await run_in_threadpool(
                verifier.verify,
                token,
                InvocationBinding(
                    request_id=parsed.request_id,
                    subject=parsed.subject,
                    workspace_id=parsed.workspace_id,
                    installation_id=parsed.installation_id,
                    connector=parsed.connector,
                    manifest_version=parsed.manifest_version,
                    tool=tool,
                ),
                raw_body,
            )
        except ReplayStoreUnavailable as exc:
            raise ConnectorError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "Ochrana proti opakování je dočasně nedostupná.",
                retryable=True,
            ) from exc
        except TokenValidationError as exc:
            raise ConnectorError(ErrorCode.UNAUTHORIZED, "Neplatná interní autorizace.") from exc

    async def invoke(request: Request) -> Response:
        started = time.monotonic()
        request_id: str | None = None
        tool_name = "unknown"
        outcome = "internal"
        provider_status: int | None = None
        try:
            token = bearer(request)
            try:
                body, raw_body = await _body(request)
                parsed = InvocationRequest.model_validate(body)
            except ValidationError as exc:
                raise ConnectorError(
                    ErrorCode.INVALID_INPUT, "Neplatný invocation request."
                ) from exc
            request_id = parsed.request_id
            tool_name = parsed.tool
            await authorize(token, parsed, parsed.tool, raw_body)
            if parsed.connector != definition.slug or parsed.manifest_version != definition.version:
                raise ConnectorError(ErrorCode.FORBIDDEN, "Invocation neodpovídá konektoru/verzi.")
            if definition.requires_secret and (
                parsed.secret_ref is None
                or parsed.secret_version is None
                or parsed.provider_credential is None
            ):
                raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Chybí odkaz na credentials.")
            if not definition.requires_secret and (
                parsed.secret_ref is not None
                or parsed.secret_version is not None
                or parsed.provider_credential is not None
            ):
                raise ConnectorError(ErrorCode.FORBIDDEN, "Tento konektor credentials nepřijímá.")
            spec = definition.tools.get(parsed.tool)
            if spec is None:
                raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj není publikovaný.")
            try:
                arguments = spec.input_model.model_validate(parsed.arguments)
            except ValidationError as exc:
                raise ConnectorError(
                    ErrorCode.INVALID_INPUT, "Neplatné argumenty nástroje."
                ) from exc
            invocation_context = _context(parsed)
            privacy_mode(invocation_context)
            result = await spec.handler(arguments, invocation_context)
            if isinstance(result, BaseModel):
                result = result.model_dump(mode="json")
            outcome = "ok"
            return JSONResponse(
                SuccessResponse(request_id=parsed.request_id, result=result).model_dump(mode="json")
            )
        except ConnectorError as exc:
            outcome = exc.code.value
            provider_status = exc.provider_status
            return _error(request_id, exc)
        except Exception:
            # Do not log the exception text: third-party errors can contain URLs or secrets.
            outcome = ErrorCode.INTERNAL.value
            return _error(
                request_id,
                ConnectorError(ErrorCode.INTERNAL, "Interní chyba konektoru."),
            )
        finally:
            logger.info(
                "connector_invocation",
                extra={
                    "connector": definition.slug,
                    "tool": tool_name,
                    "request_id": request_id or "unknown",
                    "outcome": outcome,
                    "provider_status": provider_status,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )

    async def test_connection(request: Request) -> Response:
        request_id: str | None = None
        started = time.monotonic()
        outcome = "internal"
        provider_status: int | None = None
        try:
            token = bearer(request)
            try:
                body, raw_body = await _body(request)
                parsed = TestConnectionRequest.model_validate(body)
            except ValidationError as exc:
                raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatný test request.") from exc
            request_id = parsed.request_id
            await authorize(token, parsed, "test_connection", raw_body)
            if definition.test_connection is None:
                raise ConnectorError(ErrorCode.NOT_FOUND, "Safe test není podporovaný.")
            if parsed.connector != definition.slug or parsed.manifest_version != definition.version:
                raise ConnectorError(ErrorCode.FORBIDDEN, "Test neodpovídá konektoru/verzi.")
            if definition.requires_secret and parsed.provider_credential is None:
                raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Chybí credentials.")
            if not definition.requires_secret and parsed.provider_credential is not None:
                raise ConnectorError(ErrorCode.FORBIDDEN, "Tento konektor credentials nepřijímá.")
            result = await definition.test_connection(_context(parsed))
            if isinstance(result, BaseModel):
                result = result.model_dump(mode="json")
            outcome = "ok"
            return JSONResponse(
                SuccessResponse(request_id=parsed.request_id, result=result).model_dump(mode="json")
            )
        except ConnectorError as exc:
            outcome = exc.code.value
            provider_status = exc.provider_status
            return _error(request_id, exc)
        except Exception:
            return _error(
                request_id,
                ConnectorError(ErrorCode.INTERNAL, "Interní chyba konektoru."),
            )
        finally:
            logger.info(
                "connector_test_connection",
                extra={
                    "connector": definition.slug,
                    "request_id": request_id or "unknown",
                    "outcome": outcome,
                    "provider_status": provider_status,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )

    async def health(_: Request) -> Response:
        return JSONResponse(
            {"status": "ok", "connector": definition.slug, "version": definition.version}
        )

    async def version(_: Request) -> Response:
        return JSONResponse({"connector": definition.slug, "version": definition.version})

    async def ready(_: Request) -> Response:
        available = await run_in_threadpool(store.ready)
        return JSONResponse(
            {
                "status": "ok" if available else "unavailable",
                "connector": definition.slug,
                "version": definition.version,
            },
            status_code=200 if available else 503,
        )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            store.close()
            if definition.close is not None:
                result = definition.close()
                if inspect.isawaitable(result):
                    await result

    return Starlette(
        debug=False,
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/health/live", health, methods=["GET"]),
            Route("/health/ready", ready, methods=["GET"]),
            Route("/version", version, methods=["GET"]),
            Route("/internal/v1/invoke", invoke, methods=["POST"]),
            Route("/internal/v1/test-connection", test_connection, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
