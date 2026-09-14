"""Every registered adapter exposes the same offline ``local`` CLI entry point."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from scripts.connector_inventory import load_connectors

ROOT = Path(__file__).resolve().parents[1]


def run_module(
    slug: str, *args: str, stdin: bytes = b"", env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        **(env or {}),
    }
    completed = subprocess.run(
        [sys.executable, "-m", f"connector_{slug}", *args],
        input=stdin,
        capture_output=True,
        env=environment,
        timeout=60,
        check=False,
    )
    return completed.returncode, completed.stdout.decode(), completed.stderr.decode()


@pytest.mark.parametrize("slug", load_connectors(ROOT))
def test_local_tools_listing_matches_the_manifest(slug: str) -> None:
    status, stdout, stderr = run_module(slug, "local", "tools")
    assert status == 0, stderr
    listing = json.loads(stdout)
    assert listing["connector"] == slug
    manifest = yaml.safe_load((ROOT / slug / "connector.yaml").read_text())
    assert {tool["name"] for tool in listing["tools"]} == {
        tool["name"] for tool in manifest["tools"]
    }
    assert all(tool["read_only"] for tool in listing["tools"])


@pytest.mark.parametrize("slug", load_connectors(ROOT))
def test_local_call_without_credentials_fails_closed_and_offline(slug: str) -> None:
    listing = json.loads(run_module(slug, "local", "tools")[1])
    tool = listing["tools"][0]["name"]
    status, stdout, stderr = run_module(slug, "local", "call", tool, stdin=b"{}")
    assert stdout == ""
    error = json.loads(stderr)
    assert error["ok"] is False
    if slug == "ares":
        # Public registry: no credentials, but the test image has no network.
        assert status in (1, 2)
    else:
        assert status == 1
        assert error["error"]["code"] == "credential_invalid"
        assert "OPENMCP_LOCAL_CREDENTIALS_FILE" in error["error"]["message"]


def test_unknown_argument_is_rejected_before_starting_the_runtime() -> None:
    status, stdout, stderr = run_module("ares", "serve-please")
    assert status != 0 and stdout == "" and "local" in stderr


@pytest.mark.parametrize("slug", load_connectors(ROOT))
def test_mcp_stdio_initialize_and_tools_list_match_the_manifest(slug: str, tmp_path: Path) -> None:
    env: dict[str, str] = {}
    if slug != "ares":
        # Private connectors refuse to serve without the credentials contract.
        status, stdout, stderr = run_module(slug, "mcp", stdin=b"")
        assert status == 1 and stdout == ""
        assert json.loads(stderr)["error"]["code"] == "credential_invalid"
        manifest = yaml.safe_load((ROOT / slug / "connector.yaml").read_text())
        keys = [item["key"] for item in manifest["credentials"] if item["key"] != "pii_key"]
        # OAuth-delegated adapters (Dotykačka) list no static keys; the file must
        # still be a non-empty object so the server starts and can list tools.
        keys = keys or ["placeholder"]
        credentials = tmp_path / "credentials.json"
        credentials.write_text(json.dumps({key: "synthetic-value-0123456789" for key in keys}))
        credentials.chmod(0o600)
        env["OPENMCP_LOCAL_CREDENTIALS_FILE"] = str(credentials)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = b"".join(json.dumps(m).encode() + b"\n" for m in messages)
    status, stdout, stderr = run_module(slug, "mcp", stdin=stdin, env=env)
    assert status == 0, stderr
    replies = [json.loads(line) for line in stdout.splitlines() if line]
    assert replies[0]["result"]["serverInfo"]["name"] == f"openmcp-connector-{slug}"
    manifest = yaml.safe_load((ROOT / slug / "connector.yaml").read_text())
    assert {tool["name"] for tool in replies[1]["result"]["tools"]} == {
        tool["name"] for tool in manifest["tools"]
    }
    assert all(t["annotations"]["readOnlyHint"] for t in replies[1]["result"]["tools"])
