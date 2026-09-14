from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_ecomail import schemas as s
from connector_ecomail.app import build_definition
from connector_ecomail.service import SLUG, EcomailService, email_segment

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
API_KEY = "synthetic-api-key-0123456789abcdef"


def context(**changes: Any) -> InvocationContext:
    values = {"api_key": API_KEY, "pii_key": PII_KEY}
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
    "list_lists": ({}, "/lists", {}),
    "get_list": ({"list_id": 42}, "/lists/42", {}),
    "list_subscribers": (
        {"list_id": 75, "page": 3, "per_page": 100, "status": "unsubscribed"},
        "/lists/75/subscribers",
        {"page": "3", "per_page": "100", "status": "unsubscribed"},
    ),
    "get_subscriber": (
        {"email": "john.doe+x@example.test"},
        "/subscribers/john.doe+x@example.test",
        {},
    ),
    "list_campaigns": (
        {
            "per_page": 50,
            "sort_by": "sent_at",
            "sort_dir": "asc",
            "campaign_id": 9,
            "title": "Jarní akce",
            "subject": "Sleva",
            "status": 3,
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
        },
        "/campaigns",
        {
            "per_page": "50",
            "sort_by": "sent_at",
            "sort_dir": "asc",
            "filters[id]": "9",
            "filters[title]": "Jarní akce",
            "filters[subject]": "Sleva",
            "filters[status]": "3",
            "filters[date_from]": "2024-01-01",
            "filters[date_to]": "2024-12-31",
        },
    ),
    "campaign_stats": (
        {"campaign_id": 12, "from_date": "2024-01-01", "to_date": "2024-01-31"},
        "/campaigns/12/stats",
        {"from_date": "2024-01-01", "to_date": "2024-01-31"},
    ),
    "list_templates": ({}, "/templates", {}),
    "list_automations": ({}, "/pipelines", {}),
}


def upstream_payload(request: httpx.Request) -> Any:
    if request.url.path.startswith("/subscribers/"):
        return {"subscriber": {"name": "Private", "email": "private@example.test"}}
    if request.url.path.endswith("/stats"):
        return {"stats": {"inject": 10, "open": 5}}
    return [{"id": 1, "name": "Private list", "from_email": "private@example.test"}]


@pytest.mark.anyio
async def test_all_tools_use_documented_paths_key_header_and_params() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=upstream_payload(request))

    definition = build_definition(EcomailService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, path, params) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.host == "api2.ecomailapp.cz" and request.url.path == path
        assert dict(request.url.params) == params
        assert request.headers["key"] == API_KEY
        assert "authorization" not in request.headers
        assert request.headers["user-agent"].startswith("OpenMCP/")
        text = result.model_dump_json()
        assert API_KEY not in text and PII_KEY not in text
        assert "Private" not in text and "private@example.test" not in text


@pytest.mark.anyio
async def test_subscriber_in_list_uses_list_scoped_path() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"subscriber": {"id": 1}})

    spec = build_definition(EcomailService(transport=httpx.MockTransport(upstream))).tools[
        "get_subscriber"
    ]
    arguments = spec.input_model.model_validate({"email": "a@example.test", "list_id": 42})
    await spec.handler(arguments, context())
    assert seen[0].url.path == "/lists/42/subscriber/a@example.test"
    assert seen[0].url.raw_path == b"/lists/42/subscriber/a@example.test"


def test_email_segment_accepts_plain_addresses_only() -> None:
    assert email_segment("a.b+c@example.test") == "a.b%2Bc@example.test"
    for value in ("../x", "a b@example.test", "@example.test", "person", "x@y/z", "a@b?c"):
        with pytest.raises(ConnectorError):
            email_segment(value)


@pytest.mark.anyio
async def test_test_connection_reads_lists() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    service = EcomailService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/lists" and seen[0].headers["key"] == API_KEY


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"secret_ref": f"{SLUG}/other/install-1"}, ErrorCode.FORBIDDEN),
        ({"secret_version": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": {}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"pii_key": "short"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": "short"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": "key with spaces 0123456789"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"api_key": "line\nbreak-0123456789abc"}}, ErrorCode.CREDENTIAL_INVALID),
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
        return httpx.Response(200, json=[])

    service = EcomailService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_lists"]
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
        (httpx.Response(200, json={"errors": ["private detail"]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"error": "private detail"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json="private detail"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, content=b"<html>private detail</html>"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(400, json={"message": "private detail"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json={"message": "private detail"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, json={"message": "private detail"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"errors": ["private detail"]}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json={"message": "private detail"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private detail"), ErrorCode.UPSTREAM_UNAVAILABLE),
    ],
)
@pytest.mark.anyio
async def test_provider_failures_never_leak_provider_text(
    response: httpx.Response, code: ErrorCode
) -> None:
    service = EcomailService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["get_list"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"list_id": 1}), context())
    assert caught.value.code is code
    assert "private detail" not in str(caught.value)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"list_id": 0},
        {"list_id": "1"},
        {"list_id": True},
        {"list_id": 1, "page": 0},
        {"list_id": 1, "per_page": 0},
        {"list_id": 1, "per_page": 501},
        {"list_id": 1, "per_page": "20"},
        {"list_id": 1, "status": "all"},
        {"list_id": 1, "status": ""},
        {"list_id": 1, "url": "https://outside.invalid"},
        {"list_id": 1, "key": "other"},
        [],
        "text",
        None,
    ],
)
def test_subscriber_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.SubscriberList.model_validate(arguments)
    assert s.SubscriberList.model_validate({"list_id": 1}).status == "subscribed"


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.Empty, {"page": 1}),
        (s.ListID, {}),
        (s.ListID, {"list_id": -1}),
        (s.SubscriberDetail, {}),
        (s.SubscriberDetail, {"email": "not-an-email"}),
        (s.SubscriberDetail, {"email": "a@b c"}),
        (s.SubscriberDetail, {"email": "a@example.test", "list_id": 0}),
        (s.CampaignList, {"per_page": 201}),
        (s.CampaignList, {"sort_by": "recipients"}),
        (s.CampaignList, {"sort_dir": "up"}),
        (s.CampaignList, {"status": 101}),
        (s.CampaignList, {"date_from": "1.1.2024"}),
        (s.CampaignList, {"title": "x\ny"}),
        (s.CampaignStats, {}),
        (s.CampaignStats, {"campaign_id": 1, "from_date": "2024-1-1"}),
        (s.CampaignStats, {"campaign_id": "1"}),
    ],
)
def test_other_arguments_are_closed_and_bounded(
    model: type[s.Input], arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
