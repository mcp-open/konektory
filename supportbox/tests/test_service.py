from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_supportbox.app import build_definition
from connector_supportbox.service import SLUG, SupportboxService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
TOKEN = "synthetic-supportbox-api-key"


def context(**changes: Any) -> InvocationContext:
    values = {"api_token": TOKEN, "pii_key": PII_KEY}
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
    "list_mail_tickets": (
        {
            "page": 2,
            "per_page": 10,
            "status": "pending",
            "mailbox_id": 3,
            "unassigned": True,
            "tag_id": 9,
            "created_from": "2024-01-01",
            "created_to": "2024-01-31T23:59:59",
            "last_message_from": "2024-01-15T00:00:00",
        },
        "/api/rest/v2/mail-tickets",
    ),
    "get_mail_ticket": ({"ticket_id": 42}, "/api/rest/v2/mail-tickets"),
    "list_mail_ticket_messages": ({"ticket_id": 42}, "/api/rest/v2/mail-tickets/42/messages"),
    "list_mailboxes": ({}, "/api/rest/v2/mailboxes"),
    "list_users": ({"per_page": 50}, "/api/rest/v2/users"),
    "list_tags": ({}, "/api/rest/v2/tags"),
}


@pytest.mark.anyio
async def test_all_tools_use_documented_paths_bearer_and_pseudonymize() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "items": [{"id": 42, "status": "new", "sender_email": "private@example.test"}],
                "pagination": {"page": 1, "per_page": 25, "total": 1, "total_pages": 1},
            },
        )

    definition = build_definition(SupportboxService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, path) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.host == "app.supportbox.cz" and request.url.path == path
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)
        text = result.model_dump_json()
        assert "private@example.test" not in text and TOKEN not in text and PII_KEY not in text
        assert result.data["items"][0]["id"] == 42
        assert result.data["total"] == 1 and result.data["pages"] == 1
    tickets = seen[0].url.params
    assert tickets["page"] == "2" and tickets["per_page"] == "10"
    assert tickets["filter[status][eq]"] == "pending"
    assert tickets["filter[mailbox_id][eq]"] == "3"
    assert tickets["filter[assigned_user_id][eq]"] == "null"
    assert tickets["filter[tag_id][eq]"] == "9"
    assert tickets["filter[created_at][gte]"] == "2024-01-01"
    assert tickets["filter[created_at][lte]"] == "2024-01-31T23:59:59"
    assert tickets["filter[last_message_at][gte]"] == "2024-01-15T00:00:00"
    assert "filter[last_message_at][lte]" not in tickets
    assert seen[1].url.params["filter[id][eq]"] == "42"
    assert seen[2].url.params["per_page"] == "25" and "filter[id][eq]" not in seen[2].url.params
    assert seen[4].url.params["per_page"] == "50"


@pytest.mark.anyio
async def test_test_connection_reads_one_user() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [], "pagination": {"total": 0}})

    service = SupportboxService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/api/rest/v2/users" and seen[0].url.params["per_page"] == "1"


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"api_token": ""}},
        {"credentials": {"api_token": "bad token"}},
        {"credentials": {"api_token": "tok\r\nX: y"}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_version": None},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"items": []})

    service = SupportboxService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(401, json={"message": "private auth"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"message": "private"}), ErrorCode.NOT_FOUND),
        (httpx.Response(422, json={"errors": [{"field": "private"}]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(502, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"not json private"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[{"id": 1}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"error": "private", "items": []}), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = SupportboxService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["get_mail_ticket"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"ticket_id": 1}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_mail_tickets", {"page": 0}),
        ("list_mail_tickets", {"per_page": 51}),
        ("list_mail_tickets", {"per_page": "10"}),
        ("list_mail_tickets", {"status": "trash"}),
        ("list_mail_tickets", {"mailbox_id": 0}),
        ("list_mail_tickets", {"mailbox_id": True}),
        ("list_mail_tickets", {"assigned_user_id": 1, "unassigned": True}),
        ("list_mail_tickets", {"created_from": "2024-13-01"}),
        ("list_mail_tickets", {"created_from": "yesterday"}),
        ("list_mail_tickets", {"created_from": ""}),
        ("list_mail_tickets", {"filter[status][eq]": "new"}),
        ("list_mail_tickets", {"url": "https://evil.test"}),
        ("get_mail_ticket", {}),
        ("get_mail_ticket", {"ticket_id": "42"}),
        ("get_mail_ticket", {"ticket_id": -1}),
        ("list_mail_ticket_messages", {"ticket_id": 1, "sort": "asc"}),
        ("list_users", []),
        ("list_tags", None),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
