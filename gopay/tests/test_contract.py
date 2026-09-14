"""Manifest drift and the real signed HTTP boundary of the runtime."""

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

from connector_gopay.app import build_definition, create_runtime_app
from connector_gopay.service import ORIGINS, SLUG, VERSION, GopayService

SIGNING_KEY = "synthetic-runtime-signing-key-0123456789abcdef"
PII_KEY = "synthetic-runtime-pii-key-0123456789abcdef"
TOKEN = "synthetic-access-token-0123456789abcdef"
TOOL = "get_payment"


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
        {"key": key, "required": True, "secret": True}
        for key in ("goid", "client_id", "client_secret", "environment", "pii_key")
    ]
    runtime = manifest["runtime"]
    assert runtime["transport"] == "internal_http"
    assert runtime["internal_auth"] == "hmac_sha256_compact_v1"
    assert runtime["internal_auth_env"] == "OPENMCP_INTERNAL_TOKEN"
    assert runtime["internal_auth_file_env"] == "OPENMCP_INTERNAL_TOKEN_FILE"
    assert runtime["internal_auth_source_policy"] == "exactly_one"
    assert runtime["internal_auth_max_ttl_seconds"] == 60
    assert manifest["egress"] == {
        "hosts": ["gate.gopay.cz", "gw.sandbox.gopay.com"],
        "port": 443,
        "path_prefix": "/api",
        "methods": ["GET", "POST"],
    }
    hosts = [origin.removeprefix("https://") for origin in ORIGINS.values()]
    assert manifest["egress"]["hosts"] == hosts
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


def test_signed_boundary_fails_closed_and_pseudonymises_provider_data() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth2/token":
            assert request.method == "POST"
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "bearer"})
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "id": 3123456789,
                "state": "PAID",
                "payer": {"contact": {"email": "secret@example.test"}},
            },
        )

    service = GopayService(transport=httpx.MockTransport(upstream))
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
        "arguments": {"payment_id": 3123456789},
        "secret_ref": f"{SLUG}/ws-1/inst-1",
        "secret_version": 1,
        "provider_credential": {
            "goid": "8123456789",
            "client_id": "1061399163",
            "client_secret": "synthetic-secret",
            "environment": "sandbox",
            "pii_key": PII_KEY,
        },
    }
    path = "/internal/v1/invoke"
    with TestClient(runtime) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/version").json() == {"connector": SLUG, "version": VERSION}
        assert client.post(path, content=raw(body)).status_code == 401
        assert client.post(path, json={}).status_code == 401
        headers = signed(body)
        tampered = body | {"provider_credential": {"pii_key": "changed"}}
        assert client.post(path, content=raw(tampered), headers=headers).status_code == 401
        foreign = body | {"secret_ref": f"{SLUG}/foreign/inst-1"}
        assert client.post(path, content=raw(foreign), headers=signed(foreign)).status_code == 403
        response = client.post(path, content=raw(body), headers=headers)
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert "secret@example.test" not in response.text
        assert PII_KEY not in response.text and "synthetic-secret" not in response.text
        assert TOKEN not in response.text
        # A consumed token is never accepted twice.
        assert client.post(path, content=raw(body), headers=headers).status_code == 401
        for unknown in ("create_payment", "refund_payment", "delete_card"):
            write = body | {"tool": unknown}
            assert client.post(path, content=raw(write), headers=signed(write)).status_code == 404
        test_body = {key: value for key, value in body.items() if key not in {"tool", "arguments"}}
        test_path = "/internal/v1/test-connection"
        assert client.post(test_path, content=raw(test_body)).status_code == 401
        test_response = client.post(
            test_path, content=raw(test_body), headers=signed(test_body, tool="test_connection")
        )
        assert test_response.status_code == 200
        assert client.post("/mcp", json={}).status_code == 404
