"""Manifest drift and the real signed HTTP boundary of the Sklik runtime."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from openmcp_connector_runtime import InMemoryReplayStore, create_app
from starlette.testclient import TestClient

from connector_sklik.app import build_definition, create_runtime_app
from connector_sklik.service import ALLOWED_METHODS, API_PREFIX, SLUG, VERSION, SklikService

SIGNING_KEY = "synthetic-runtime-signing-key-0123456789abcdef"
PII_KEY = "synthetic-runtime-pii-key-0123456789abcdef"
API_TOKEN = "synthetic-sklik-api-token-0123456789abcdef"
SESSION = "synthetic-session-0123456789"
TOOL = "list_campaigns"


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def raw(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def signed(body: dict[str, Any], *, tool: str | None = None) -> dict[str, str]:
    now = int(time.time())
    header = encoded(raw({"alg": "HS256", "typ": "OMCP-INV", "v": 1}))
    claims = {
        key: body[key]
        for key in (
            "request_id",
            "subject",
            "workspace_id",
            "installation_id",
            "connector",
            "manifest_version",
        )
    } | {
        "iss": "openmcp-core",
        "aud": f"connector:{body['connector']}",
        "iat": now,
        "exp": now + 45,
        "jti": secrets.token_urlsafe(18),
        "tool": tool or body["tool"],
        "body_sha256": encoded(hashlib.sha256(raw(body)).digest()),
    }
    message = f"{header}.{encoded(raw(claims))}"
    signature = encoded(hmac.new(SIGNING_KEY.encode(), message.encode(), hashlib.sha256).digest())
    return {"Authorization": f"Bearer {message}.{signature}", "Content-Type": "application/json"}


def test_manifest_matches_runtime_definition() -> None:
    definition = build_definition()
    manifest_path = Path(__file__).parents[1] / "connector.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["slug"] == definition.slug == SLUG
    assert manifest["version"] == definition.version == VERSION == "1.0.0"
    assert manifest["capabilities"] == {
        "default_read_only": True,
        "supports_write": False,
        "supports_test": definition.test_connection is not None,
        "pii_pseudonymization": "configurable",
        "privacy_modes": ["strict", "balanced", "plain"],
        "default_privacy_mode": "strict",
    }
    assert manifest["credentials"] == [
        {"key": "api_token", "required": True, "secret": True},
        {"key": "user_id", "required": False, "secret": True},
        {"key": "pii_key", "required": True, "secret": True},
    ]
    runtime = manifest["runtime"]
    assert runtime["transport"] == "internal_http"
    assert runtime["internal_auth"] == "hmac_sha256_compact_v1"
    assert runtime["internal_auth_env"] == "OPENMCP_INTERNAL_TOKEN"
    assert runtime["internal_auth_file_env"] == "OPENMCP_INTERNAL_TOKEN_FILE"
    assert runtime["internal_auth_source_policy"] == "exactly_one"
    assert runtime["internal_auth_max_ttl_seconds"] == 60
    # API Drak JSON is POST-only; the service allow-list is the whole set of methods sent.
    assert manifest["egress"] == {
        "host": "api.sklik.cz",
        "port": 443,
        "path_prefix": "/drak/json/v5",
        "methods": ["POST"],
    }
    assert sorted(ALLOWED_METHODS) == [
        "ads.list",
        "campaigns.createReport",
        "campaigns.list",
        "campaigns.readReport",
        "client.get",
        "client.loginByToken",
        "client.logout",
        "groups.list",
        "keywords.list",
    ]
    assert manifest["tools"] == [
        {
            "name": tool.name,
            "description": tool.description,
            "read_only": tool.read_only,
            "input_schema": tool.input_model.model_json_schema(),
        }
        for tool in definition.tools.values()
    ]
    assert all(tool.read_only for tool in definition.tools.values())


def test_runtime_uses_shared_sdk_signing_key_file_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing_key = tmp_path / "signing-key"
    signing_key.write_text("synthetic-private-signing-key-with-32-bytes\n", encoding="utf-8")
    monkeypatch.delenv("OPENMCP_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("OPENMCP_INTERNAL_TOKEN_FILE", str(signing_key))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPENMCP_REPLAY_STORE", "memory")

    with TestClient(create_runtime_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["connector"] == SLUG


def test_signed_boundary_fails_closed_and_tool_returns_pseudonymized_data() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        method = request.url.path.removeprefix(API_PREFIX + "/")
        if method == "client.loginByToken":
            return httpx.Response(200, json={"status": 200, "session": SESSION})
        if method == "client.logout":
            return httpx.Response(200, json={"status": 200})
        assert method in ("client.get", "campaigns.list")
        return httpx.Response(
            200,
            json={
                "status": 200,
                "session": SESSION,
                "campaigns": [{"id": 1, "name": "Private campaign"}],
                "user": {"userId": 1, "username": "private"},
            },
        )

    service = SklikService(transport=httpx.MockTransport(upstream))
    runtime = create_app(
        build_definition(service), signing_key=SIGNING_KEY, replay_store=InMemoryReplayStore()
    )
    body = {
        "request_id": "req-1",
        "subject": "user-1",
        "workspace_id": "ws-1",
        "installation_id": "inst-1",
        "connector": SLUG,
        "manifest_version": VERSION,
        "tool": TOOL,
        "arguments": {"limit": 5},
        "secret_ref": f"{SLUG}/ws-1/inst-1",
        "secret_version": 1,
        "provider_credential": {"api_token": API_TOKEN, "pii_key": PII_KEY},
    }
    path = "/internal/v1/invoke"
    with TestClient(runtime) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/version").json() == {"connector": SLUG, "version": VERSION}
        assert client.post(path, content=raw(body)).status_code == 401
        assert client.post(path, json={}).status_code == 401
        headers = signed(body)
        tampered = body | {"provider_credential": {"api_token": "x", "pii_key": "changed"}}
        assert client.post(path, content=raw(tampered), headers=headers).status_code == 401
        foreign = body | {"secret_ref": f"{SLUG}/foreign/inst-1"}
        assert client.post(path, content=raw(foreign), headers=signed(foreign)).status_code == 403
        assert seen == []
        response = client.post(path, content=raw(body), headers=headers)
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["result"]["data"]["count"] == 1
        assert "Private" not in response.text and SESSION not in response.text
        assert PII_KEY not in response.text and API_TOKEN not in response.text
        assert [request.url.path.removeprefix(API_PREFIX + "/") for request in seen] == [
            "client.loginByToken",
            "campaigns.list",
            "client.logout",
        ]
        assert all(request.method == "POST" for request in seen)
        # A consumed token is never accepted twice.
        assert client.post(path, content=raw(body), headers=headers).status_code == 401
        for unknown in ("delete_all", "create_campaign", f"{TOOL}_write", "update_campaign"):
            write = body | {"tool": unknown}
            assert client.post(path, content=raw(write), headers=signed(write)).status_code == 404
        bad = body | {"arguments": {"url": "https://evil.invalid"}}
        assert client.post(path, content=raw(bad), headers=signed(bad)).status_code == 400
        assert len(seen) == 3
        test_body = {key: value for key, value in body.items() if key not in {"tool", "arguments"}}
        test_path = "/internal/v1/test-connection"
        assert client.post(test_path, content=raw(test_body)).status_code == 401
        test_response = client.post(
            test_path, content=raw(test_body), headers=signed(test_body, tool="test_connection")
        )
        assert test_response.status_code == 200
        assert test_response.json()["ok"] is True
        assert client.post("/mcp", json={}).status_code == 404
