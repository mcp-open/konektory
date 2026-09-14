from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_collabim.app import build_definition
from connector_collabim.service import PATHS, SLUG, CollabimService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
API_KEY = "synthetic-collabim-user-key-0123456789"


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
    "list_projects": (
        {"name_like": "shop", "active": True, "page": 2, "items_per_page": 50},
        "/projects",
        {"nameLike": "shop", "active": "1", "page": "2", "itemsPerPage": "50"},
    ),
    "get_project": ({"project_id": 7}, "/projects/7", {}),
    "list_keywords": (
        {"project_id": 7, "keyword_like": "boty", "tags": "brand", "starred": False},
        "/keywords",
        {
            "projectId": "7",
            "keywordLike": "boty",
            "tags": "brand",
            "starred": "0",
            "page": "1",
            "itemsPerPage": "20",
        },
    ),
    "keyword_positions": (
        {
            "project_id": 7,
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "project_keyword_ids": [1, 2],
            "get_x_days": 5,
        },
        "/keyword-positions",
        {
            "projectId": "7",
            "from": "2024-01-01",
            "to": "2024-01-31",
            "projectKeywordIds": "1,2",
            "getXDays": "5",
        },
    ),
    "aggregated_positions": (
        {"project_id": 7, "date_from": "2024-01-01", "date_to": "2024-03-31", "tags": "brand"},
        "/aggregated-keywords-positions",
        {"projectId": "7", "from": "2024-01-01", "to": "2024-03-31", "tags": "brand"},
    ),
    "position_distribution": (
        {"project_id": 7, "date_from": "2024-01-01", "date_to": "2024-01-31", "tag_name": "t"},
        "/position-distribution",
        {"projectId": "7", "from": "2024-01-01", "to": "2024-01-31", "tagName": "t"},
    ),
    "market_share": (
        {
            "project_id": 7,
            "search_engine_id": 1,
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
        },
        "/market-share",
        {"projectId": "7", "searchEngineId": "1", "from": "2024-01-01", "to": "2024-01-31"},
    ),
    "list_activities": (
        {
            "project_id": 7,
            "added_on_from": "2024-01-01",
            "added_on_to": "2024-02-01",
            "type_id": 1,
            "state_id": 2,
        },
        "/activities",
        {
            "projectId": "7",
            "addedOnFrom": "2024-01-01",
            "addedOnTo": "2024-02-01",
            "typeId": "1",
            "stateId": "2",
            "page": "1",
            "itemsPerPage": "20",
        },
    ),
}


@pytest.mark.anyio
async def test_all_tools_get_documented_paths_with_user_key_header() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/projects/7":
            return httpx.Response(200, json={"data": {"id": 7, "name": "Private project"}})
        return httpx.Response(
            200, json={"data": [{"id": 1, "keyword": "private keyword", "position": 3}]}
        )

    definition = build_definition(CollabimService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, path, params) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.host == "api.oncollabim.com" and request.url.path == path
        assert path == PATHS[name].replace("{id}", "7")
        assert dict(request.url.params) == params
        assert request.headers["authorization"] == API_KEY
        assert request.headers["accept"] == "application/collabim+json"
        assert request.headers["user-agent"].startswith("OpenMCP/")
        text = result.model_dump_json()
        assert API_KEY not in text and PII_KEY not in text and "rivate" not in text
        if name != "get_project":
            assert result.data["count"] == 1
    assert len(seen) == len(CASES)


@pytest.mark.anyio
async def test_test_connection_reads_one_project_page() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    service = CollabimService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/projects"
    assert dict(seen[0].url.params) == {"page": "1", "itemsPerPage": "1"}


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"api_key": "short"}},
        {"credentials": {"api_key": "bad key with spaces 0123456789"}},
        {"credentials": {"api_key": ""}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_version": None},
        {"provider_credential": None},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": []})

    service = CollabimService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    assert calls == 0


@pytest.mark.anyio
async def test_unknown_tool_is_refused_without_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": []})

    service = CollabimService(transport=httpx.MockTransport(upstream))
    model = build_definition().tools["list_projects"].input_model
    with pytest.raises(ConnectorError) as caught:
        await service.invoke("delete_activity", model.model_validate({}), context())
    assert caught.value.code is ErrorCode.NOT_FOUND
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, json={"errors": [{"message": "private"}]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json={"message": "private token"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, text="private rule"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, text="private"), ErrorCode.NOT_FOUND),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[{"id": 1}]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"errors": [{"message": "private"}]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"data": "private"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"message": "private"}), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response, code: ErrorCode) -> None:
    service = CollabimService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["list_projects"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is code
    assert "private" not in str(caught.value)


WINDOW = {"project_id": 7, "date_from": "2024-01-01", "date_to": "2024-01-31"}


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_projects", {"page": 0}),
        ("list_projects", {"items_per_page": 101}),
        ("list_projects", {"page": "1"}),
        ("list_projects", {"active": 1}),
        ("list_projects", {"name_like": ""}),
        ("list_projects", {"name_like": "a\r\nAuthorization: x"}),
        ("list_projects", {"url": "https://evil"}),
        ("list_projects", []),
        ("get_project", {}),
        ("get_project", {"project_id": 0}),
        ("get_project", {"project_id": True}),
        ("get_project", {"project_id": "7"}),
        ("list_keywords", {}),
        ("list_keywords", {"project_id": 7, "starred": "yes"}),
        ("keyword_positions", WINDOW),
        ("keyword_positions", WINDOW | {"project_keyword_ids": []}),
        ("keyword_positions", WINDOW | {"project_keyword_ids": [0]}),
        ("keyword_positions", WINDOW | {"project_keyword_ids": list(range(1, 202))}),
        ("keyword_positions", WINDOW | {"tags": "t", "get_x_days": 0}),
        ("keyword_positions", {"project_id": 7, "date_from": "2024-01-01", "tags": "t"}),
        ("aggregated_positions", WINDOW | {"date_to": "2023-12-31"}),
        ("aggregated_positions", WINDOW | {"date_to": "2025-01-02"}),
        ("aggregated_positions", WINDOW | {"date_from": "2024-13-01"}),
        ("aggregated_positions", WINDOW | {"date_from": "2024-1-1"}),
        ("position_distribution", WINDOW | {"tag_name": ""}),
        ("market_share", WINDOW),
        ("market_share", WINDOW | {"search_engine_id": -1}),
        ("list_activities", {"added_on_from": "2024-01-01"}),
        ("list_activities", {"added_on_from": "2024-02-01", "added_on_to": "2024-01-01"}),
        ("list_activities", {"type_id": "1"}),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)


def test_defaults_are_documented_provider_defaults() -> None:
    model = build_definition().tools["list_projects"].input_model
    parsed = model.model_validate({})
    assert parsed.model_dump() == {
        "page": 1,
        "items_per_page": 20,
        "name_like": None,
        "active": None,
    }
