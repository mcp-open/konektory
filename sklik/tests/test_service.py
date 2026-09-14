from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_sklik.app import build_definition
from connector_sklik.service import (
    ALLOWED_METHODS,
    API_PREFIX,
    READ_METHODS,
    SESSION_METHODS,
    SLUG,
    STATS_COLUMNS,
    SklikService,
)

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
API_TOKEN = "synthetic-sklik-api-token-0123456789abcdef"
SESSION = "synthetic-session-0123456789"
SESSION_2 = "synthetic-session-refreshed-0123456789"
REPORT_ID = "synthetic-report-id-42"


def context(**changes: Any) -> InvocationContext:
    values = {"api_token": API_TOKEN, "pii_key": PII_KEY}
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


def method_of(request: httpx.Request) -> str:
    assert request.url.path.startswith(API_PREFIX + "/")
    return request.url.path.removeprefix(API_PREFIX + "/")


def provider(seen: list[httpx.Request]) -> httpx.MockTransport:
    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        method = method_of(request)
        body = json.loads(request.content)
        if method == "client.loginByToken":
            assert body == API_TOKEN
            return httpx.Response(200, json={"status": 200, "session": SESSION})
        assert isinstance(body, list) and body[0]["session"] in (SESSION, SESSION_2)
        ok = {"status": 200, "statusMessage": "OK", "session": SESSION_2}
        if method == "client.logout":
            return httpx.Response(200, json={"status": 200, "statusMessage": "OK"})
        if method == "client.get":
            return httpx.Response(
                200,
                json=ok
                | {
                    "user": {"userId": 1, "username": "private@example.test", "walletCredit": 5},
                    "foreignAccounts": [{"userId": 2, "username": "private-agency", "access": "r"}],
                },
            )
        if method == "campaigns.createReport":
            return httpx.Response(200, json=ok | {"reportId": REPORT_ID, "totalCount": 3})
        if method == "campaigns.readReport":
            assert body[1] == REPORT_ID
            return httpx.Response(
                200,
                json=ok
                | {
                    "report": [
                        {"id": 11, "name": "Private campaign", "stats": [{"clicks": 4}]},
                    ]
                },
            )
        collection = method.split(".")[0]
        return httpx.Response(
            200, json=ok | {collection: [{"id": 11, "name": "Private entity", "status": "active"}]}
        )

    return httpx.MockTransport(upstream)


CASES: dict[str, tuple[dict[str, Any], str]] = {
    "get_client": ({}, "client.get"),
    "list_campaigns": ({"campaign_ids": [11, 12], "limit": 20, "offset": 40}, "campaigns.list"),
    "list_groups": ({"campaign_ids": [11], "include_deleted": True}, "groups.list"),
    "list_keywords": (
        {"group_ids": [5], "campaign_ids": [11], "keyword_ids": [9]},
        "keywords.list",
    ),
    "list_ads": ({"group_ids": [5]}, "ads.list"),
    "campaign_stats": (
        {
            "date_from": "2024-01-01",
            "date_to": "2024-03-31",
            "campaign_ids": [11],
            "granularity": "monthly",
            "limit": 10,
        },
        "campaigns.readReport",
    ),
}


@pytest.mark.anyio
async def test_every_tool_logs_in_reads_allow_listed_methods_and_logs_out() -> None:
    seen: list[httpx.Request] = []
    definition = build_definition(SklikService(transport=provider(seen)))
    for name, (arguments, read_method) in CASES.items():
        seen.clear()
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        methods = [method_of(request) for request in seen]
        assert methods[0] == "client.loginByToken" and methods[-1] == "client.logout"
        assert read_method in methods and set(methods) <= ALLOWED_METHODS
        assert all(request.method == "POST" for request in seen)
        assert all(request.url.host == "api.sklik.cz" for request in seen)
        assert all(request.headers["content-type"] == "application/json" for request in seen)
        assert all("authorization" not in request.headers for request in seen)
        assert all(request.headers["user-agent"].startswith("OpenMCP/") for request in seen)
        # The token is only ever the login body, never a query parameter or header.
        assert all(API_TOKEN not in str(request.url) for request in seen)
        text = result.model_dump_json()
        for private in (API_TOKEN, PII_KEY, SESSION, SESSION_2, "rivate"):
            assert private not in text
        if name == "get_client":
            assert len(result.data["data"]) == 2  # user + foreignAccounts (keys pseudonymized)
        else:
            assert result.data["count"] == 1
        body = json.loads(seen[1].content)
        assert body[0] == {"session": SESSION}
        if name == "list_campaigns":
            assert body[1] == {"isDeleted": False, "ids": [11, 12]}
            assert body[2] == {"offset": 40, "limit": 20}
        elif name == "list_groups":
            assert body[1] == {"campaign": {"ids": [11]}}
            assert body[2] == {"offset": 0, "limit": 100}
        elif name == "list_keywords":
            assert body[1] == {
                "isDeleted": False,
                "ids": [9],
                "campaign": {"ids": [11]},
                "group": {"ids": [5]},
            }
        elif name == "list_ads":
            assert body[1] == {"isDeleted": False, "group": {"ids": [5]}}
        elif name == "campaign_stats":
            assert methods == [
                "client.loginByToken",
                "campaigns.createReport",
                "campaigns.readReport",
                "client.logout",
            ]
            assert body[1] == {
                "dateFrom": "2024-01-01",
                "dateTo": "2024-03-31",
                "isDeleted": False,
                "ids": [11],
            }
            assert body[2] == {"statGranularity": "monthly"}
            read = json.loads(seen[2].content)
            assert read[0] == {"session": SESSION_2}  # refreshed session is reused
            assert read[1] == REPORT_ID
            assert read[2] == {
                "offset": 0,
                "limit": 10,
                "allowEmptyStatistics": True,
                "displayColumns": STATS_COLUMNS,
            }
            assert result.data["totalCount"] == 3
        # Logout always uses the latest refreshed session.
        assert json.loads(seen[-1].content) == [{"session": SESSION_2}]


@pytest.mark.anyio
async def test_managed_account_user_id_is_sent_only_when_configured() -> None:
    seen: list[httpx.Request] = []
    service = SklikService(transport=provider(seen))
    assert await service.test_connection(context(credentials={"user_id": "242911"})) == {
        "connected": True
    }
    assert [method_of(request) for request in seen] == [
        "client.loginByToken",
        "client.get",
        "client.logout",
    ]
    assert json.loads(seen[1].content) == [{"session": SESSION, "userId": 242911}]
    seen.clear()
    await service.test_connection(context(credentials={"user_id": ""}))
    assert json.loads(seen[1].content) == [{"session": SESSION}]


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"api_token": "short"}},
        {"credentials": {"api_token": "bad token with spaces 0123456789"}},
        {"credentials": {"api_token": ""}},
        {"credentials": {"user_id": "abc"}},
        {"credentials": {"user_id": "0"}},
        {"credentials": {"user_id": "1; drop"}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_version": None},
        {"provider_credential": None},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    seen: list[httpx.Request] = []
    service = SklikService(transport=provider(seen))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    assert seen == []


@pytest.mark.anyio
async def test_write_methods_and_unknown_tools_are_refused_without_request() -> None:
    seen: list[httpx.Request] = []
    service = SklikService(transport=provider(seen))
    client_holder: list[Any] = []

    for method in (
        "campaigns.create",
        "campaigns.update",
        "campaigns.remove",
        "campaigns.restore",
        "groups.create",
        "keywords.set",
        "ads.create",
        "client.stats",
        "client.getCredit",
        "campaigns.check",
    ):
        assert method not in ALLOWED_METHODS
        with pytest.raises(ConnectorError) as caught:
            values, client, session = await service._session(context())
            client_holder.append(client)
            await service._call(client, method, [{"session": session}])
        assert caught.value.code is ErrorCode.INTERNAL
        assert [method_of(request) for request in seen] == ["client.loginByToken"]
        seen.clear()
    for client in client_holder:
        await client.close()
    with pytest.raises(ConnectorError) as caught:
        await service.invoke(
            "create_campaign",
            build_definition().tools["get_client"].input_model.model_validate({}),
            context(),
        )
    assert caught.value.code is ErrorCode.NOT_FOUND
    assert seen == []
    assert READ_METHODS.isdisjoint(SESSION_METHODS)
    assert not any(
        method.split(".")[1].startswith(("create", "update", "remove", "restore", "set", "check"))
        for method in ALLOWED_METHODS - {"campaigns.createReport"}
    )


@pytest.mark.anyio
async def test_login_failure_stops_before_any_read() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": 401, "statusMessage": "private token msg"})

    service = SklikService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    assert "private" not in str(caught.value)
    assert [method_of(request) for request in seen] == ["client.loginByToken"]


@pytest.mark.anyio
async def test_read_failure_still_logs_out_and_logout_failure_is_ignored() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        method = method_of(request)
        if method == "client.loginByToken":
            return httpx.Response(200, json={"status": 200, "session": SESSION})
        if method == "client.logout":
            return httpx.Response(500, text="private outage")
        return httpx.Response(200, json={"status": 404, "statusMessage": "private not found"})

    service = SklikService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_campaigns"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is ErrorCode.NOT_FOUND
    assert "private" not in str(caught.value)
    assert [method_of(request) for request in seen][-1] == "client.logout"


def drak(status: int, **extra: Any) -> httpx.Response:
    return httpx.Response(200, json={"status": status, "statusMessage": "private"} | extra)


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, json={"status": 400}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, text="private"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, text="private"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, text="private"), ErrorCode.NOT_FOUND),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[{"status": 200}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"statusMessage": "private"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"status": "200", "session": SESSION}), ErrorCode.UPSTREAM_ERROR),
        (drak(400), ErrorCode.UPSTREAM_ERROR),
        (drak(401), ErrorCode.CREDENTIAL_INVALID),
        (drak(403), ErrorCode.CREDENTIAL_INVALID),
        (drak(406), ErrorCode.UPSTREAM_ERROR),
        (drak(413), ErrorCode.UPSTREAM_ERROR),
        (drak(429), ErrorCode.RATE_LIMITED),
        (drak(500), ErrorCode.UPSTREAM_UNAVAILABLE),
        (drak(200, session="bad session"), ErrorCode.UPSTREAM_ERROR),
        (drak(200, session=SESSION, campaigns="private"), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = SklikService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["list_campaigns"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


@pytest.mark.anyio
async def test_invalid_report_id_is_rejected_before_read_report() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        method = method_of(request)
        if method == "client.loginByToken":
            return httpx.Response(200, json={"status": 200, "session": SESSION})
        if method == "campaigns.createReport":
            return httpx.Response(200, json={"status": 200, "reportId": "../private"})
        return httpx.Response(200, json={"status": 200})

    service = SklikService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["campaign_stats"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(
            spec.input_model.model_validate({"date_from": "2024-01-01", "date_to": "2024-01-31"}),
            context(),
        )
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR and "private" not in str(caught.value)
    assert "campaigns.readReport" not in [method_of(request) for request in seen]


STATS = {"date_from": "2024-01-01", "date_to": "2024-01-31"}


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("get_client", {"limit": 1}),
        ("get_client", {"url": "https://evil"}),
        ("get_client", []),
        ("list_campaigns", {"limit": 0}),
        ("list_campaigns", {"limit": 501}),
        ("list_campaigns", {"offset": -1}),
        ("list_campaigns", {"limit": "5"}),
        ("list_campaigns", {"limit": True}),
        ("list_campaigns", {"include_deleted": "yes"}),
        ("list_campaigns", {"campaign_ids": []}),
        ("list_campaigns", {"campaign_ids": [0]}),
        ("list_campaigns", {"campaign_ids": list(range(1, 102))}),
        ("list_campaigns", {"campaign_ids": ["1"]}),
        ("list_groups", {"group_ids": "5"}),
        ("list_keywords", {"keyword_ids": [1.5]}),
        ("list_ads", {"ad_ids": [None]}),
        ("campaign_stats", {}),
        ("campaign_stats", {"date_from": "2024-01-01"}),
        ("campaign_stats", STATS | {"date_to": "2023-12-31"}),
        ("campaign_stats", STATS | {"date_to": "2025-01-02"}),
        ("campaign_stats", STATS | {"date_from": "2024-13-01"}),
        ("campaign_stats", STATS | {"date_from": "2024-1-1"}),
        ("campaign_stats", STATS | {"date_from": "2024-01-01T00:00:00"}),
        ("campaign_stats", STATS | {"granularity": "hourly"}),
        ("campaign_stats", STATS | {"limit": 0}),
        ("campaign_stats", STATS | {"session": "x"}),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
