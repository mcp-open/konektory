from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_smartemailing import schemas as s
from connector_smartemailing.app import build_definition
from connector_smartemailing.service import SLUG, SmartemailingService, contact_segment

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
USERNAME = "synthetic@example.test"
API_KEY = "synthetic-api-key-0123456789abcdef"
OK = {"status": "ok", "meta": {"total_count": 1}, "data": [{"id": 1, "name": "Private Person"}]}


def context(**changes: Any) -> InvocationContext:
    values = {"username": USERNAME, "api_key": API_KEY, "pii_key": PII_KEY}
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


CASES: dict[str, tuple[dict[str, Any], str, dict[str, str]]] = {
    "list_contacts": (
        {
            "limit": 50,
            "offset": 100,
            "select": "id,emailaddress",
            "sort": "-id,emailaddress",
            "expand": "customfields",
            "emailaddress": "person@example.test",
            "surname": "Novák",
            "country": "CZ",
            "language": "cs_CZ",
            "blacklisted": 0,
        },
        "/api/v3/contacts",
        {
            "limit": "50",
            "offset": "100",
            "select": "id,emailaddress",
            "sort": "-id,emailaddress",
            "expand": "customfields",
            "emailaddress": "person@example.test",
            "surname": "Novák",
            "country": "CZ",
            "language": "cs_CZ",
            "blacklisted": "0",
        },
    ),
    "get_contact": (
        {"contact": "person+tag@example.test", "select": "id,emailaddress"},
        "/api/v3/contacts/person+tag@example.test",
        {"select": "id,emailaddress"},
    ),
    "list_contactlists": (
        {"sort": "-id"},
        "/api/v3/contactlists",
        {"limit": "100", "offset": "0", "sort": "-id"},
    ),
    "get_contactlist": (
        {"contactlist_id": 7, "select": "id,name"},
        "/api/v3/contactlists/7",
        {"select": "id,name"},
    ),
    "list_emails": (
        {"sort": "-id"},
        "/api/v3/emails",
        {"limit": "10", "offset": "0", "select": "id,name,title,created", "sort": "-id"},
    ),
    "list_newsletters": (
        {"newsletter_id": 5, "email_id": 3, "limit": 20},
        "/api/v3/newsletter",
        {"limit": "20", "offset": "0", "filter[id][eq]": "5", "filter[email_id][eq]": "3"},
    ),
    "newsletter_stats": (
        {"select": "sent,opened"},
        "/api/v3/newsletter-stats-summary",
        {"limit": "100", "offset": "0", "select": "sent,opened"},
    ),
    "list_customfields": (
        {"expand": "customfield_options"},
        "/api/v3/customfields",
        {"limit": "100", "offset": "0", "expand": "customfield_options"},
    ),
}


@pytest.mark.anyio
async def test_all_tools_use_documented_paths_basic_auth_and_params() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK)

    definition = build_definition(SmartemailingService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, path, params) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.host == "app.smartemailing.cz" and request.url.path == path
        assert dict(request.url.params) == params
        assert request.headers["authorization"].startswith("Basic ")
        assert request.headers["user-agent"].startswith("OpenMCP/")
        text = result.model_dump_json()
        assert API_KEY not in text and PII_KEY not in text and USERNAME not in text
        assert "Private Person" not in text


def test_contact_segment_accepts_id_or_email_only() -> None:
    assert contact_segment("123") == "123"
    assert contact_segment("a.b+c@example.test") == "a.b%2Bc@example.test"
    for value in ("../x", "a b@example.test", "@example.test", "person", "x@y/z"):
        with pytest.raises(ConnectorError):
            contact_segment(value)


@pytest.mark.anyio
async def test_test_connection_uses_check_credentials() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "meta": [], "account_id": 2})

    service = SmartemailingService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/api/v3/check-credentials"
    assert seen[0].headers["authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"secret_ref": f"{SLUG}/other/install-1"}, ErrorCode.FORBIDDEN),
        ({"secret_version": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": {}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"pii_key": "short"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"username": "bad:user"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": "line\nbreak"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": ""}}, ErrorCode.CREDENTIAL_INVALID),
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_are_rejected_before_any_request(
    changes: dict[str, Any], code: ErrorCode
) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=OK)

    service = SmartemailingService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_contactlists"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context(**changes))
    assert caught.value.code is code
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code is code
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (
            httpx.Response(200, json={"status": "error", "message": "private detail"}),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (httpx.Response(200, json=[{"message": "private detail"}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, content=b"<html>private detail</html>"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(400, json={"message": "private detail"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json={"message": "private detail"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, json={"message": "private detail"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"message": "private detail"}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json={"message": "private detail"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(502, text="private detail"), ErrorCode.UPSTREAM_UNAVAILABLE),
    ],
)
@pytest.mark.anyio
async def test_provider_failures_never_leak_provider_text(
    response: httpx.Response, code: ErrorCode
) -> None:
    service = SmartemailingService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["list_contacts"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is code
    assert "private detail" not in str(caught.value)


@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"limit": "5"},
        {"limit": True},
        {"select": ""},
        {"select": "id;drop"},
        {"select": "Id"},
        {"sort": "id desc"},
        {"expand": "everything"},
        {"emailaddress": "not-an-email"},
        {"emailaddress": "a@b c"},
        {"language": "cs"},
        {"blacklisted": 2},
        {"blacklisted": "0"},
        {"name": "x\ny"},
        {"url": "https://outside.invalid"},
        {"username": "other"},
        [],
        "text",
        None,
    ],
)
def test_contact_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.ContactList.model_validate(arguments)
    assert s.ContactList.model_validate({}).limit == 100


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.ContactDetail, {}),
        (s.ContactDetail, {"contact": "person"}),
        (s.ContactDetail, {"contact": "../admin"}),
        (s.ContactDetail, {"contact": 7}),
        (s.ContactlistID, {}),
        (s.ContactlistID, {"contactlist_id": 0}),
        (s.ContactlistID, {"contactlist_id": "7"}),
        (s.EmailList, {"limit": 11}),
        (s.EmailList, {"select": "id,body"}),
        (s.EmailList, {"sort": "created"}),
        (s.NewsletterList, {"newsletter_id": 0}),
        (s.NewsletterList, {"email_id": True}),
        (s.NewsletterStats, {"sort": "sent"}),
        (s.CustomfieldList, {"expand": "customfields"}),
    ],
)
def test_other_arguments_are_closed_and_bounded(
    model: type[s.Input], arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
