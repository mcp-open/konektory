from __future__ import annotations

import io
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr

from openmcp_connector_runtime import (
    ConnectorDefinition,
    InvocationContext,
    PrivacyMode,
    ToolSpec,
)
from openmcp_connector_runtime.errors import ConnectorError, ErrorCode
from openmcp_connector_runtime.local_cli import run_local_cli
from openmcp_connector_runtime.provider import privacy_mode, private_envelope


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


async def handler(arguments: BaseModel, context: InvocationContext) -> dict[str, Any]:
    parsed = Input.model_validate(arguments)
    return {"value": parsed.value, "workspace": context.workspace_id}


def definition(*, requires_secret: bool = False) -> ConnectorDefinition:
    return ConnectorDefinition(
        "demo",
        "1.0.0",
        {"echo": ToolSpec("echo", Input, handler, "Vrátí zadanou hodnotu.")},
        requires_secret=requires_secret,
    )


def execute(
    args: list[str], raw: bytes = b""
) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = run_local_cli(
        definition(), args, stdin=io.BytesIO(raw), stdout=stdout, stderr=stderr
    )
    return (
        status,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def test_tools_lists_only_the_direct_read_only_contract() -> None:
    status, output, error = execute(["tools"])
    assert status == 0
    assert error is None
    assert output is not None
    assert output["connector"] == "demo"
    assert output["tools"] == [
        {
            "name": "echo",
            "description": "Vrátí zadanou hodnotu.",
            "read_only": True,
            "input_schema": {
                "additionalProperties": False,
                "properties": {"value": {"title": "Value", "type": "integer"}},
                "required": ["value"],
                "title": "Input",
                "type": "object",
            },
        }
    ]


def test_call_reads_json_from_stdin_and_uses_local_scope() -> None:
    status, output, error = execute(["call", "echo"], b'{"value":7}')
    assert status == 0
    assert error is None
    assert output == {"ok": True, "result": {"value": 7, "workspace": "local"}}


def test_call_rejects_invalid_or_duplicate_json_without_dispatch() -> None:
    for raw in (b"not-json", b"[]", b'{"value":1,"value":2}', b'{"value":"bad"}'):
        status, output, error = execute(["call", "echo"], raw)
        assert status == 2
        assert output is None
        assert error is not None
        assert error["ok"] is False
        assert error["error"]["code"] == "invalid_input"


def test_private_connector_cannot_accidentally_bypass_credential_contract() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = run_local_cli(
        definition(requires_secret=True),
        ["call", "echo"],
        stdin=io.BytesIO(b'{"value":7}'),
        stdout=stdout,
        stderr=stderr,
    )
    assert status == 1
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "credential_invalid"


# --- file-backed credentials for private connectors -----------------------------


def private_definition(seen: list[InvocationContext]) -> ConnectorDefinition:
    async def private_handler(arguments: BaseModel, context: InvocationContext) -> dict[str, Any]:
        seen.append(context)
        secret = (context.provider_credential or {})["api_key"].get_secret_value()
        return {"value": Input.model_validate(arguments).value, "key_len": len(secret)}

    return ConnectorDefinition(
        "demo",
        "1.0.0",
        {"echo": ToolSpec("echo", Input, private_handler, "Vrátí zadanou hodnotu.")},
        requires_secret=True,
    )


def execute_private(
    tmp_path: Any, args: list[str], raw: bytes, credentials: object, mode: int = 0o600
) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None, list[InvocationContext]]:
    seen: list[InvocationContext] = []
    path = tmp_path / "credentials.json"
    path.write_text(credentials if isinstance(credentials, str) else json.dumps(credentials))
    path.chmod(mode)
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = run_local_cli(
        private_definition(seen),
        args,
        stdin=io.BytesIO(raw),
        stdout=stdout,
        stderr=stderr,
        environ={"OPENMCP_LOCAL_CREDENTIALS_FILE": str(path)},
    )
    return (
        status,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
        seen,
    )


def test_private_connector_reads_owner_only_credentials_file(tmp_path: Any) -> None:
    status, output, error, seen = execute_private(
        tmp_path, ["call", "echo"], b'{"value":7}', {"api_key": "synthetic-key"}
    )
    assert status == 0, error
    assert output == {"ok": True, "result": {"value": 7, "key_len": len("synthetic-key")}}
    context = seen[0]
    assert context.secret_ref == "demo/local/local"
    assert context.secret_version == 1
    assert context.provider_credential is not None
    # An ephemeral pii_key is generated so SDK credential helpers accept the context.
    assert len(context.provider_credential["pii_key"].get_secret_value()) >= 32
    assert "synthetic-key" not in json.dumps(output)


def test_private_connector_keeps_a_supplied_pii_key(tmp_path: Any) -> None:
    pii = "stable-local-pii-key-0123456789abcdef"
    _, _, _, seen = execute_private(
        tmp_path, ["call", "echo"], b'{"value":1}', {"api_key": "k", "pii_key": pii}
    )
    assert seen[0].provider_credential is not None
    assert seen[0].provider_credential["pii_key"].get_secret_value() == pii


def test_private_connector_rejects_missing_or_unsafe_credentials(tmp_path: Any) -> None:
    for credentials, mode in (
        ({"api_key": "k"}, 0o644),
        ("not-json", 0o600),
        ("[]", 0o600),
        ("{}", 0o600),
        ('{"api_key": 5}', 0o600),
        ('{"api_key": ""}', 0o600),
        ('{"api_key": "a", "api_key": "b"}', 0o600),
    ):
        status, output, error, seen = execute_private(
            tmp_path, ["call", "echo"], b'{"value":7}', credentials, mode
        )
        assert status == 1, (credentials, mode)
        assert output is None
        assert error is not None and error["error"]["code"] == "credential_invalid"
        assert seen == [], "credentials must be validated before any dispatch"

    stdout, stderr = io.StringIO(), io.StringIO()
    status = run_local_cli(
        private_definition([]),
        ["call", "echo"],
        stdin=io.BytesIO(b'{"value":7}'),
        stdout=stdout,
        stderr=stderr,
        environ={},
    )
    assert status == 1
    assert "OPENMCP_LOCAL_CREDENTIALS_FILE" in json.loads(stderr.getvalue())["error"]["message"]


def test_credentials_never_travel_on_argv_or_environment_values() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    status = run_local_cli(
        private_definition([]),
        ["call", "echo"],
        stdin=io.BytesIO(b'{"value":7}'),
        stdout=stdout,
        stderr=stderr,
        environ={"OPENMCP_LOCAL_CREDENTIALS_FILE": "relative/credentials.json"},
    )
    assert status == 1
    assert json.loads(stderr.getvalue())["error"]["code"] == "credential_invalid"


# --- plaintext opt-in ------------------------------------------------------------


def test_privacy_mode_is_bound_to_each_local_call(tmp_path: Any) -> None:
    observed: list[PrivacyMode] = []

    async def probe(arguments: BaseModel, context: InvocationContext) -> dict[str, Any]:
        observed.append(privacy_mode(context))
        return {"ok": True}

    definition = ConnectorDefinition(
        "demo", "1.0.0", {"echo": ToolSpec("echo", Input, probe, "Sonda.")}
    )
    run_local_cli(definition, ["call", "--plain", "echo"], stdin=io.BytesIO(b'{"value":1}'),
                  stdout=io.StringIO(), stderr=io.StringIO(), environ={})
    run_local_cli(definition, ["call", "--privacy", "balanced", "echo"],
                  stdin=io.BytesIO(b'{"value":1}'), stdout=io.StringIO(),
                  stderr=io.StringIO(), environ={})
    run_local_cli(definition, ["call", "echo"], stdin=io.BytesIO(b'{"value":1}'),
                  stdout=io.StringIO(), stderr=io.StringIO(), environ={})
    assert observed == [PrivacyMode.PLAIN, PrivacyMode.BALANCED, PrivacyMode.STRICT]

    pii = "stable-local-pii-key-0123456789abcdef"
    context = InvocationContext(
        request_id="r", subject="s", workspace_id="w", installation_id="i",
        secret_ref="demo/w/i", secret_version=1,
        provider_credential={"api_key": SecretStr("top-secret-token"), "pii_key": SecretStr(pii)},
    )
    payload = {"customer_name": "Jana Nováková", "id": 7, "token": "top-secret-token"}
    masked = private_envelope(payload, context, "demo", "scope", pii, "https://x.test/")
    assert "Jana Nováková" not in masked.model_dump_json()
    plain = private_envelope(
        payload,
        context.model_copy(update={"runtime_flags": {"privacy_mode": "plain"}}),
        "demo", "scope", pii, "https://x.test/",
    )
    assert plain.data["customer_name"] == "Jana Nováková" and plain.data["id"] == 7
    assert "top-secret-token" not in plain.model_dump_json(), "secrets stay masked in plain mode"
    assert plain.warnings and "bez pseudonymizace" in plain.warnings[0]


def test_conflicting_privacy_flags_are_rejected() -> None:
    try:
        run_local_cli(definition(), ["call", "echo", "--privacy", "balanced", "--plain"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("conflicting privacy flags were accepted")


def test_error_envelope_carries_provider_status_but_no_provider_text() -> None:
    from openmcp_connector_runtime.app import _error

    response = _error(
        "req-1",
        ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Poskytovatel odmítl autorizaci.", provider_status=403
        ),
    )
    body = json.loads(bytes(response.body))
    assert body["error"] == {
        "code": "credential_invalid",
        "message": "Poskytovatel odmítl autorizaci.",
        "retryable": False,
        "provider_status": 403,
    }


def test_runtime_logging_emits_bounded_structured_lines(capsys: Any) -> None:
    import logging

    from openmcp_connector_runtime.runner import configure_logging

    configure_logging()
    configure_logging()  # idempotent: one handler
    logger = logging.getLogger("openmcp.connector")
    assert sum(1 for h in logger.handlers if getattr(h, "_openmcp_structured", False)) == 1
    logger.info(
        "connector_test_connection",
        extra={"connector": "demo", "request_id": "r1", "outcome": "credential_invalid",
               "provider_status": 401, "duration_ms": 12, "password": "must-not-appear"},
    )
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["msg"] == "connector_test_connection"
    assert line["connector"] == "demo" and line["provider_status"] == 401
    assert "password" not in line and "must-not-appear" not in json.dumps(line)
