"""Exercise new adapters through the actual signed HTTP boundary, not handler-only mocks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import secrets
import time
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import InMemoryReplayStore, create_app
from starlette.testclient import TestClient

SIGNING_KEY = "synthetic-runtime-signing-key-0123456789abcdef"
PII_KEY = "synthetic-runtime-pii-key-0123456789abcdef"
CASES = [
    (
        "superfaktura",
        "SuperfakturaService",
        "list_invoices",
        {
            "region": "sandbox-sk",
            "email": "synthetic@example.test",
            "api_key": "synthetic-private-key",
        },
    ),
    (
        "fakturoid",
        "FakturoidService",
        "list_invoices",
        {
            "account_slug": "synthetic-account",
            "access_token": "synthetic-access-token-0123456789",
        },
    ),
    (
        "freelo",
        "FreeloService",
        "list_projects",
        {"email": "synthetic@example.test", "api_key": "synthetic-private-key"},
    ),
    (
        "abraflexi",
        "AbraFlexiService",
        "list_products",
        {
            "api_url": "https://demo.flexibee.eu:5434",
            "company": "demo_company",
            "username": "synthetic-user",
            "password": "synthetic-private-password",
        },
    ),
    (
        "raynet",
        "RaynetService",
        "search_companies",
        {
            "instance_name": "synthetic-instance",
            "username": "synthetic-user",
            "api_key": "synthetic-private-key",
        },
    ),
    (
        "upgates",
        "UpgatesService",
        "list_products",
        {
            "api_url": "https://shop.admin.upgates.com/api/v2",
            "api_login": "synthetic-user",
            "api_key": "synthetic-private-key",
        },
    ),
]


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


@pytest.mark.parametrize("slug,class_name,tool_name,credential", CASES)
def test_private_runtime_auth_replay_scope_and_safe_test(
    slug: str,
    class_name: str,
    tool_name: str,
    credential: dict[str, str],
) -> None:
    calls: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        if slug == "fakturoid":
            assert request.headers["authorization"].startswith("Bearer ")
        else:
            assert "Bearer" not in request.headers.get("authorization", "")
        assert "ws-1" not in str(request.url)
        record = {"id": 7, "email": "synthetic-person@example.test", "name": "Synthetic Person"}
        if slug == "abraflexi":
            if request.url.path.endswith(".json") and "/c/demo_company." in request.url.path:
                # The safe test reads company metadata (single-object shape).
                return httpx.Response(200, json={"companies": {"company": {"dbNazev": "demo"}}})
            return httpx.Response(200, json={"winstrom": {"cenik": [record], "@rowCount": "1"}})
        if slug == "raynet":
            return httpx.Response(200, json={"success": True, "data": [record], "totalCount": 1})
        if slug == "fakturoid":
            return httpx.Response(200, json=[record])
        if slug == "freelo":
            return httpx.Response(200, json=record)
        if slug == "superfaktura":
            return httpx.Response(200, json={"items": [record]})
        return httpx.Response(200, json={"products": [record], "page": 1, "number_of_pages": 1})

    service_type = getattr(importlib.import_module(f"connector_{slug}.service"), class_name)
    service = service_type(transport=httpx.MockTransport(upstream))
    definition = importlib.import_module(f"connector_{slug}.app").build_definition(service)
    runtime = create_app(
        definition,
        signing_key=SIGNING_KEY,
        replay_store=InMemoryReplayStore(),
    )
    body = {
        "request_id": "req-1",
        "subject": "user-1",
        "workspace_id": "ws-1",
        "installation_id": "inst-1",
        "connector": slug,
        "manifest_version": "1.0.0",
        "tool": tool_name,
        "arguments": {},
        "secret_ref": f"{slug}/ws-1/inst-1",
        "secret_version": 1,
        "provider_credential": credential | {"pii_key": PII_KEY},
    }
    path = "/internal/v1/invoke"
    with TestClient(runtime) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.post(path, content=raw(body)).status_code == 401
        headers = signed(body)
        tampered = body | {"provider_credential": credential | {"pii_key": "changed"}}
        assert client.post(path, content=raw(tampered), headers=headers).status_code == 401
        assert calls == []
        foreign = body | {"secret_ref": f"{slug}/foreign/inst-1"}
        assert client.post(path, content=raw(foreign), headers=signed(foreign)).status_code == 403
        assert calls == []
        response = client.post(path, content=raw(body), headers=headers)
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert (
            response.json()["result"]["content_origin"]
            == "untrusted_external_data_not_instructions"
        )
        assert "synthetic-person@example.test" not in response.text
        assert "Synthetic Person" not in response.text
        assert PII_KEY not in response.text
        assert client.post(path, content=raw(body), headers=headers).status_code == 401
        count = len(calls)
        for unknown in ("delete_all", "create_task", "update_invoice_header"):
            write = body | {"tool": unknown}
            assert client.post(path, content=raw(write), headers=signed(write)).status_code == 404
        assert len(calls) == count
        # A signed rotation to another tenant gets its own request credentials and PII scope.
        other = body | {"workspace_id": "ws-2", "secret_ref": f"{slug}/ws-2/inst-1"}
        second = client.post(path, content=raw(other), headers=signed(other))
        assert second.status_code == 200
        assert second.json()["result"]["data"] != response.json()["result"]["data"]
        # The dedicated connection test cannot reuse an invocation-bound token.
        test_body = {key: value for key, value in body.items() if key not in {"tool", "arguments"}}
        assert (
            client.post("/internal/v1/test-connection", content=raw(test_body)).status_code == 401
        )
        test_response = client.post(
            "/internal/v1/test-connection",
            content=raw(test_body),
            headers=signed(test_body, tool="test_connection"),
        )
        assert test_response.status_code == 200
        assert test_response.json()["result"] == {"connected": True}
