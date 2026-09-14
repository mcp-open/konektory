from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_daktela.app import build_definition
from connector_daktela.service import SLUG, DaktelaService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
TOKEN = "synthetic-static-access-token"


def context(**changes: Any) -> InvocationContext:
    values = {
        "instance_url": "https://tenant.daktela.com",
        "access_token": TOKEN,
        "pii_key": PII_KEY,
    }
    values.update(changes.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="subject-1",
        workspace_id="workspace-1",
        installation_id="install-1",
        secret_ref=f"{SLUG}/workspace-1/install-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=changes)


CASES: dict[str, tuple[dict[str, Any], str]] = {
    "list_tickets": (
        {
            "take": 5,
            "skip": 10,
            "stage": "OPEN",
            "title_contains": "audit",
            "created_from": "2024-01-01",
            "created_to": "2024-01-31",
        },
        "/api/v6/tickets.json",
    ),
    "get_ticket": ({"name": "1234"}, "/api/v6/tickets/1234.json"),
    "list_activities": ({"ticket": "1234", "type": "EMAIL"}, "/api/v6/activities.json"),
    "list_contacts": ({"lastname_contains": "Nov"}, "/api/v6/contacts.json"),
    "get_contact": ({"name": "john_smith"}, "/api/v6/contacts/john_smith.json"),
    "list_queues": ({"type": "email"}, "/api/v6/queues.json"),
    "list_users": ({}, "/api/v6/users.json"),
}


@pytest.mark.anyio
async def test_all_tools_use_tenant_origin_header_token_and_pseudonymize() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {"data": [{"name": "1234", "title": "Private ticket title"}], "total": 1},
                "_time": "2024-01-01 00:00:00",
            },
        )

    definition = build_definition(DaktelaService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, path) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.host == "tenant.daktela.com" and request.url.path == path
        assert request.headers["x-auth-token"] == TOKEN
        assert "accessToken" not in request.url.params
        assert TOKEN not in str(request.url)
        text = result.model_dump_json()
        assert "Private ticket title" not in text and TOKEN not in text and PII_KEY not in text
        assert result.data["total"] == 1 and len(result.data["data"]) == 1
    tickets = seen[0].url.params
    assert tickets["take"] == "5" and tickets["skip"] == "10"
    assert tickets["filter[0][field]"] == "stage" and tickets["filter[0][value]"] == "OPEN"
    assert tickets["filter[1][operator]"] == "contains"
    assert tickets["filter[2][value]"] == "2024-01-01 00:00:00"
    assert tickets["filter[3][value]"] == "2024-01-31 23:59:59"
    assert tickets["sort[0][field]"] == "created" and tickets["sort[0][dir]"] == "desc"
    assert seen[2].url.params["filter[0][field]"] == "ticket"
    assert seen[6].url.params["take"] == "20" and "filter[0][field]" not in seen[6].url.params


@pytest.mark.anyio
async def test_test_connection_uses_whoim() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"error": [], "result": {"user": {"name": "me"}}})

    service = DaktelaService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/api/v6/whoim.json"


@pytest.mark.parametrize(
    "credentials",
    [
        {"instance_url": "http://tenant.daktela.com"},
        {"instance_url": "https://tenant.daktela.com.evil.test"},
        {"instance_url": "https://evil.test/?x=tenant.daktela.com"},
        {"instance_url": "https://a.b.daktela.com"},
        {"instance_url": "https://tenant.daktela.com/api/v6"},
        {"access_token": "bad token"},
        {"access_token": ""},
        {"pii_key": "short"},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(
    credentials: dict[str, str],
) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"error": [], "result": []})

    service = DaktelaService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(credentials=credentials))
    assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert calls == 0


@pytest.mark.anyio
async def test_foreign_installation_is_forbidden() -> None:
    with pytest.raises(ConnectorError) as caught:
        await DaktelaService().test_connection(context(secret_ref=f"{SLUG}/other/install-1"))
    assert caught.value.code is ErrorCode.FORBIDDEN


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(401, json={"error": "private token detail"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"error": ["Not found private"]}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(503, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"<html>private</html>"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[1, 2]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"error": ["private failure"]}), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = DaktelaService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["get_ticket"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"name": "1"}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_tickets", {"take": 0}),
        ("list_tickets", {"take": 101}),
        ("list_tickets", {"skip": -1}),
        ("list_tickets", {"take": "5"}),
        ("list_tickets", {"stage": "DONE"}),
        ("list_tickets", {"created_from": "2024-13-01"}),
        ("list_tickets", {"created_from": "2024-1-1"}),
        ("list_tickets", {"title_contains": ""}),
        ("list_tickets", {"url": "https://evil.test"}),
        ("list_tickets", {"user": "john smith"}),
        ("list_tickets", {"user": "../users"}),
        ("get_ticket", {}),
        ("get_ticket", {"name": "1234.json"}),
        ("get_ticket", {"name": 1234}),
        ("get_contact", {"name": "evil\r\nX: y"}),
        ("list_activities", {"type": "PIGEON"}),
        ("list_queues", {"type": "unknown"}),
        ("list_users", {"filter": "x"}),
        ("list_users", []),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
