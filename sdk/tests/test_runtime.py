from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from starlette.testclient import TestClient

from openmcp_connector_runtime import (
    ConnectorDefinition,
    InMemoryReplayStore,
    ReplayStoreUnavailable,
    ToolSpec,
    create_app,
)

TOKEN = "test-internal-token-that-is-long-enough"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


async def handler(arguments: BaseModel, context: Any) -> dict[str, Any]:
    parsed = Input.model_validate(arguments)
    return {"value": parsed.value, "workspace": context.workspace_id}


def payload() -> dict[str, Any]:
    return {
        "request_id": "req-1",
        "subject": "user-1",
        "workspace_id": "ws-1",
        "installation_id": "inst-1",
        "connector": "demo",
        "manifest_version": "1.0.0",
        "tool": "echo",
        "arguments": {"value": 7},
    }


def app():
    spec = ToolSpec("echo", Input, handler, "Echo")
    return create_app(
        ConnectorDefinition("demo", "1.0.0", {"echo": spec}),
        signing_key=TOKEN,
        replay_store=InMemoryReplayStore(),
    )


def _segment(value: object) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def authorization(body: dict[str, Any], *, tool: str | None = None, jti: str | None = None) -> str:
    now = int(time.time())
    header = _segment({"alg": "HS256", "typ": "OMCP-INV", "v": 1})
    claims = _segment(
        {
            "iss": "openmcp-core",
            "aud": f"connector:{body['connector']}",
            "iat": now,
            "exp": now + 45,
            "jti": jti or secrets.token_urlsafe(18),
            "request_id": body["request_id"],
            "subject": body["subject"],
            "workspace_id": body["workspace_id"],
            "installation_id": body["installation_id"],
            "connector": body["connector"],
            "manifest_version": body["manifest_version"],
            "tool": tool or body["tool"],
            "body_sha256": base64.urlsafe_b64encode(
                hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).digest()
            )
            .rstrip(b"=")
            .decode(),
        }
    )
    signing_input = f"{header}.{claims}"
    signature = hmac.new(TOKEN.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"Bearer {signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def test_invocation_requires_internal_bearer() -> None:
    with TestClient(app()) as client:
        assert client.post("/internal/v1/invoke", json=payload()).status_code == 401


def test_invocation_rejects_credentials_for_no_secret_connector() -> None:
    body = payload() | {"secret_ref": "x", "secret_version": 1}
    with TestClient(app()) as client:
        response = client.post(
            "/internal/v1/invoke",
            json=body,
            headers={"Authorization": authorization(body)},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_invocation_validates_tool_input() -> None:
    body = payload() | {"arguments": {"value": "bad"}}
    with TestClient(app()) as client:
        response = client.post(
            "/internal/v1/invoke",
            json=body,
            headers={"Authorization": authorization(body)},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"


def test_success_response_and_health() -> None:
    body = payload()
    with TestClient(app()) as client:
        response = client.post(
            "/internal/v1/invoke",
            json=body,
            headers={"Authorization": authorization(body)},
        )
        health = client.get("/healthz")
    assert response.json() == {
        "request_id": "req-1",
        "ok": True,
        "result": {"value": 7, "workspace": "ws-1"},
    }
    assert health.json()["status"] == "ok"


def test_raw_shared_key_is_not_an_authorization_credential() -> None:
    with TestClient(app()) as client:
        response = client.post(
            "/internal/v1/invoke",
            json=payload(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert response.status_code == 401


def test_runtime_rejects_public_signing_key_placeholder() -> None:
    spec = ToolSpec("echo", Input, handler, "Echo")
    with pytest.raises(RuntimeError, match="public placeholder"):
        create_app(
            ConnectorDefinition("demo", "1.0.0", {"echo": spec}),
            signing_key="REPLACE_WITH_PUBLIC_EXAMPLE_SIGNING_KEY_123456",
        )


def test_signed_invocation_is_exactly_one_use() -> None:
    body = payload()
    headers = {"Authorization": authorization(body)}
    with TestClient(app()) as client:
        assert client.post("/internal/v1/invoke", json=body, headers=headers).status_code == 200
        replay = client.post("/internal/v1/invoke", json=body, headers=headers)
    assert replay.status_code == 401


def test_signed_invocation_rejects_body_tampering_before_dispatch() -> None:
    original = payload()
    tampered = original | {"arguments": {"value": 8}}
    with TestClient(app()) as client:
        response = client.post(
            "/internal/v1/invoke",
            json=tampered,
            headers={"Authorization": authorization(original)},
        )
    assert response.status_code == 401


def test_test_connection_uses_dedicated_tool_binding() -> None:
    async def safe_test(context: Any) -> dict[str, str]:
        return {"installation": context.installation_id}

    spec = ToolSpec("echo", Input, handler, "Echo")
    runtime = create_app(
        ConnectorDefinition("demo", "1.0.0", {"echo": spec}, test_connection=safe_test),
        signing_key=TOKEN,
        replay_store=InMemoryReplayStore(),
    )
    body = {
        "request_id": "req-test-1",
        "subject": "user-1",
        "workspace_id": "ws-1",
        "installation_id": "inst-1",
        "connector": "demo",
        "manifest_version": "1.0.0",
        "secret_ref": "demo/ws-1/inst-1",
        "secret_version": 1,
    }
    with TestClient(runtime) as client:
        response = client.post(
            "/internal/v1/test-connection",
            json=body,
            headers={"Authorization": authorization(body, tool="test_connection")},
        )
    assert response.status_code == 200
    assert response.json()["result"] == {"installation": "inst-1"}


class UnavailableReplayStore:
    def __init__(self) -> None:
        self.closed = False

    def consume(self, jti: str, expires_at: int, now: int) -> bool:
        raise ReplayStoreUnavailable("test outage")

    def ready(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


def test_replay_store_outage_fails_closed_before_tool_dispatch() -> None:
    dispatched = False

    async def tracked_handler(arguments: BaseModel, context: Any) -> dict[str, bool]:
        nonlocal dispatched
        dispatched = True
        return {"dispatched": True}

    spec = ToolSpec("echo", Input, tracked_handler, "Echo")
    store = UnavailableReplayStore()
    runtime = create_app(
        ConnectorDefinition("demo", "1.0.0", {"echo": spec}),
        signing_key=TOKEN,
        replay_store=store,
    )
    body = payload()
    with TestClient(runtime) as client:
        ready = client.get("/health/ready")
        response = client.post(
            "/internal/v1/invoke",
            json=body,
            headers={"Authorization": authorization(body)},
        )
    assert ready.status_code == 503
    assert ready.json()["status"] == "unavailable"
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "upstream_unavailable",
        "message": "Ochrana proti opakování je dočasně nedostupná.",
        "retryable": True,
        "provider_status": None,
    }
    assert dispatched is False
    assert store.closed is True
