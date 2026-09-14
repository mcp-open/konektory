from __future__ import annotations

import io
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from openmcp_connector_runtime import ConnectorDefinition, InvocationContext, ToolSpec
from openmcp_connector_runtime.mcp_stdio import SUPPORTED_PROTOCOL_VERSIONS, run_mcp_stdio


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


async def echo(arguments: BaseModel, context: InvocationContext) -> dict[str, Any]:
    parsed = Input.model_validate(arguments)
    return {"value": parsed.value, "workspace": context.workspace_id}


def definition(*, requires_secret: bool = False) -> ConnectorDefinition:
    return ConnectorDefinition(
        "demo",
        "1.0.0",
        {"echo": ToolSpec("echo", Input, echo, "Vrátí zadanou hodnotu.")},
        requires_secret=requires_secret,
    )


def session(
    messages: list[object], *, args: list[str] | None = None, environ: dict[str, str] | None = None,
    private: bool = False,
) -> tuple[int, list[dict[str, Any]], str]:
    raw = b"".join(
        (m if isinstance(m, bytes) else json.dumps(m).encode()) + b"\n" for m in messages
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    status = run_mcp_stdio(
        definition(requires_secret=private), args or [], stdin=io.BytesIO(raw),
        stdout=stdout, stderr=stderr, environ=environ or {},
    )
    replies = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    return status, replies, stderr.getvalue()


def rpc(method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


INIT = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "0"}})
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_initialize_echoes_supported_version_and_lists_read_only_tools() -> None:
    status, replies, stderr = session([INIT, INITIALIZED, rpc("tools/list", request_id=2)])
    assert status == 0 and stderr == ""
    init, listing = replies
    assert init["id"] == 1
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"] == {"name": "openmcp-connector-demo", "version": "1.0.0"}
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    tools = listing["result"]["tools"]
    assert [t["name"] for t in tools] == ["echo"]
    assert tools[0]["inputSchema"]["additionalProperties"] is False
    assert tools[0]["annotations"]["readOnlyHint"] is True
    assert tools[0]["annotations"]["destructiveHint"] is False


def test_unknown_or_old_protocol_version_falls_back_to_newest() -> None:
    _, replies, _ = session([rpc("initialize", {"protocolVersion": "1999-01-01"})])
    assert replies[0]["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_tools_call_returns_text_and_structured_content() -> None:
    _, replies, _ = session([
        INIT, INITIALIZED,
        rpc("tools/call", {"name": "echo", "arguments": {"value": 7}}, request_id="abc"),
    ])
    call = replies[-1]
    assert call["id"] == "abc"
    assert call["result"]["isError"] is False
    assert json.loads(call["result"]["content"][0]["text"]) == {"value": 7, "workspace": "local"}
    assert call["result"]["structuredContent"] == {"value": 7, "workspace": "local"}


def test_tool_failures_are_results_not_protocol_errors() -> None:
    _, replies, _ = session([
        INIT, INITIALIZED,
        rpc("tools/call", {"name": "echo", "arguments": {"value": "bad"}}, request_id=2),
        rpc("tools/call", {"name": "missing", "arguments": {}}, request_id=3),
        rpc("tools/call", {"name": "echo", "arguments": []}, request_id=4),
    ])
    invalid, missing, shape = replies[1:]
    assert invalid["result"]["isError"] is True
    assert json.loads(invalid["result"]["content"][0]["text"])["code"] == "invalid_input"
    assert missing["error"]["code"] == -32602
    assert shape["error"]["code"] == -32602


def test_protocol_guards() -> None:
    _, replies, _ = session([
        b"not json",
        [rpc("ping")],
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        rpc("tools/call", {"name": "echo", "arguments": {"value": 1}}, request_id=5),
        rpc("nope", request_id=6),
        rpc("ping", request_id=7),
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
        {"jsonrpc": "2.0", "id": 99, "result": {}},
    ])
    codes = [(r.get("id"), r["error"]["code"]) for r in replies if "error" in r]
    assert codes[:3] == [(None, -32700), (None, -32600), (None, -32600)]
    assert (5, -32600) in codes, "tools/call before initialized must be refused"
    assert (6, -32601) in codes
    assert any(r.get("id") == 7 and r.get("result") == {} for r in replies)
    assert not any(r.get("id") == 99 for r in replies)


def test_private_connector_needs_credentials_file_before_serving() -> None:
    status, replies, stderr = session([INIT], private=True)
    assert status == 1 and replies == []
    assert json.loads(stderr)["error"]["code"] == "credential_invalid"


def test_plain_alias_and_balanced_mode_are_accepted() -> None:
    status, replies, _ = session([INIT, INITIALIZED, rpc("ping", request_id=2)], args=["--plain"])
    assert status == 0 and replies[-1]["result"] == {}
    status, replies, _ = session(
        [INIT, INITIALIZED, rpc("ping", request_id=2)],
        args=["--privacy", "balanced"],
    )
    assert status == 0 and replies[-1]["result"] == {}


def test_stdin_close_ends_the_session_cleanly() -> None:
    status, replies, stderr = session([])
    assert status == 0 and replies == [] and stderr == ""
