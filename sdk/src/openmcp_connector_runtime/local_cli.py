"""Direct, non-MCP command line access to a connector definition."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable, Sequence
from typing import Any, BinaryIO, TextIO, cast

from pydantic import BaseModel, SecretStr, ValidationError
from starlette.applications import Starlette

from .app import ConnectorDefinition
from .auth import TokenValidationError, loads_json_no_duplicates
from .errors import ConnectorError, ErrorCode
from .models import InvocationContext
from .provider import PrivacyMode
from .runner import run

_MAX_INPUT_BYTES = 64 * 1024
_MAX_CREDENTIALS_BYTES = 64 * 1024
CREDENTIALS_FILE_ENV = "OPENMCP_LOCAL_CREDENTIALS_FILE"
LOCAL_SCOPE = "local"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local",
        description="Spustí jeden read-only nástroj konektoru přímo, bez MCP a OpenMCP core.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tools", help="Vypíše dostupné lokální nástroje jako JSON.")
    call = commands.add_parser("call", help="Načte JSON argumenty ze stdin a zavolá nástroj.")
    call.add_argument("tool", help="Přesný název read-only nástroje.")
    modes = call.add_mutually_exclusive_group()
    modes.add_argument(
        "--privacy",
        choices=[mode.value for mode in PrivacyMode],
        default=PrivacyMode.STRICT.value,
        help="Režim ochrany provider dat (výchozí: strict).",
    )
    modes.add_argument(
        "--plain",
        action="store_true",
        help=(
            "Vrátí odpověď providera bez pseudonymizace. Jen pro vlastní data na"
            " vlastním stroji; výstup pak obsahuje osobní údaje."
        ),
    )
    return parser


def _write_json(stream: TextIO, value: object) -> None:
    json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")


def _error(code: ErrorCode, message: str, *, retryable: bool = False) -> dict[str, object]:
    return {
        "ok": False,
        "error": {"code": code.value, "message": message, "retryable": retryable},
    }


def _read_credentials_file(environ: dict[str, str]) -> dict[str, SecretStr]:
    """Load provider credentials from a private JSON file, never from argv or env values.

    The file must be an absolute path to a regular, non-symlink file readable only by
    its owner. Values are strings keyed exactly like ``connector.yaml`` credentials;
    ``pii_key`` is optional and, when absent, an ephemeral per-process key is used so
    pseudonymised tokens stay consistent only within one call.
    """
    path = environ.get(CREDENTIALS_FILE_ENV, "")
    if not path:
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID,
            f"Nastavte {CREDENTIALS_FILE_ENV} na soubor s credentials konektoru.",
        )
    if not os.path.isabs(path) or "\x00" in path:
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Cesta ke credentials musí být absolutní."
        )
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Soubor s credentials nelze číst."
        ) from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CREDENTIALS_BYTES:
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Credentials musí být běžný soubor do 64 KiB."
        )
    if os.name == "posix" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID,
            "Soubor s credentials smí být čitelný jen pro vlastníka (chmod 600).",
        )
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_CREDENTIALS_BYTES + 1)
    except OSError as exc:
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Soubor s credentials nelze číst."
        ) from exc
    try:
        parsed = loads_json_no_duplicates(raw)
    except TokenValidationError as exc:
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Credentials nejsou platný JSON objekt."
        ) from exc
    if (
        not isinstance(parsed, dict)
        or not parsed
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            or len(value.encode()) > 4096
            for key, value in parsed.items()
        )
    ):
        raise ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Credentials musí být objekt neprázdných řetězců."
        )
    values = {key: SecretStr(value) for key, value in parsed.items()}
    if "pii_key" not in values:
        values["pii_key"] = SecretStr(secrets.token_hex(32))
    return values


def _read_arguments(stream: BinaryIO) -> dict[str, Any]:
    raw = stream.read(_MAX_INPUT_BYTES + 1)
    if len(raw) > _MAX_INPUT_BYTES:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Vstup je příliš velký.")
    try:
        parsed = loads_json_no_duplicates(raw)
    except TokenValidationError as exc:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Vstup není platný JSON.") from exc
    if not isinstance(parsed, dict):
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Vstup musí být JSON objekt.")
    return parsed


async def _close(definition: ConnectorDefinition) -> None:
    if definition.close is None:
        return
    result = definition.close()
    if inspect.isawaitable(result):
        await result


def local_context(
    definition: ConnectorDefinition,
    environ: dict[str, str],
    mode: PrivacyMode = PrivacyMode.STRICT,
) -> InvocationContext:
    """Installation-bound local context; private connectors need the credentials file."""
    credential = _read_credentials_file(environ) if definition.requires_secret else None
    return InvocationContext(
        request_id="local-cli",
        subject="local-user",
        workspace_id=LOCAL_SCOPE,
        installation_id=LOCAL_SCOPE,
        # The SDK credential helper binds secrets to exactly this local installation.
        secret_ref=f"{definition.slug}/{LOCAL_SCOPE}/{LOCAL_SCOPE}" if credential else None,
        secret_version=1 if credential else None,
        provider_credential=credential,
        runtime_flags={"privacy_mode": mode.value},
    )


async def invoke_local_tool(
    definition: ConnectorDefinition,
    tool_name: str,
    arguments: dict[str, Any],
    environ: dict[str, str],
    mode: PrivacyMode = PrivacyMode.STRICT,
) -> object:
    """Validate and run one read-only tool with the local credential contract."""
    spec = definition.tools.get(tool_name)
    if spec is None:
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj není publikovaný.")
    if not spec.read_only:
        raise ConnectorError(ErrorCode.FORBIDDEN, "Lokálně lze spouštět jen read-only nástroje.")
    context = local_context(definition, environ, mode)
    try:
        validated = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatné argumenty nástroje.") from exc
    result = await spec.handler(validated, context)
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    return result


async def _call(
    definition: ConnectorDefinition,
    tool_name: str,
    stdin: BinaryIO,
    environ: dict[str, str],
    mode: PrivacyMode,
) -> object:
    if definition.tools.get(tool_name) is None:
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj není publikovaný.")
    return await invoke_local_tool(definition, tool_name, _read_arguments(stdin), environ, mode)


def run_local_cli(
    definition: ConnectorDefinition,
    argv: Sequence[str] | None = None,
    *,
    stdin: BinaryIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    """Run a connector directly and return a process-style exit code.

    Private connectors read their credentials from the file named by
    ``OPENMCP_LOCAL_CREDENTIALS_FILE``; secrets are never accepted on argv.
    ``--privacy`` selects a per-invocation data protection mode. ``--plain`` is
    retained as a compatibility alias for ``--privacy plain``.
    """

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    source = stdin or cast(BinaryIO, sys.stdin.buffer)
    env = dict(os.environ) if environ is None else environ
    args = _parser().parse_args(list(argv) if argv is not None else None)
    mode = PrivacyMode.PLAIN if getattr(args, "plain", False) else PrivacyMode(
        getattr(args, "privacy", PrivacyMode.STRICT.value)
    )

    async def execute() -> int:
        try:
            if args.command == "tools":
                _write_json(
                    output,
                    {
                        "connector": definition.slug,
                        "version": definition.version,
                        "tools": [
                            {
                                "name": spec.name,
                                "description": spec.description,
                                "read_only": spec.read_only,
                                "input_schema": spec.input_model.model_json_schema(),
                            }
                            for spec in definition.tools.values()
                            if spec.read_only
                        ],
                    },
                )
                return 0
            result = await _call(definition, args.tool, source, env, mode)
            _write_json(output, {"ok": True, "result": result})
            return 0
        except ConnectorError as exc:
            _write_json(errors, _error(exc.code, exc.message, retryable=exc.retryable))
            return 2 if exc.code in {ErrorCode.INVALID_INPUT, ErrorCode.NOT_FOUND} else 1
        except Exception:
            _write_json(errors, _error(ErrorCode.INTERNAL, "Interní chyba konektoru."))
            return 1
        finally:
            await _close(definition)

    return asyncio.run(execute())


def run_connector_main(
    build_definition: Callable[[], ConnectorDefinition],
    create_runtime_app: Callable[[], Starlette],
    *,
    default_port: int,
    argv: Sequence[str] | None = None,
) -> None:
    """Shared ``python -m connector_<slug>`` entry: runtime server, ``local`` CLI or ``mcp``."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["local"]:
        raise SystemExit(run_local_cli(build_definition(), arguments[1:]))
    if arguments[:1] == ["mcp"]:
        from .mcp_stdio import run_mcp_stdio

        raise SystemExit(run_mcp_stdio(build_definition(), arguments[1:]))
    if arguments:
        raise SystemExit(
            "Použití: python -m <connector> [local {tools|call [--plain] <tool>} | mcp [--plain]]"
        )
    run(create_runtime_app(), default_port=default_port)
