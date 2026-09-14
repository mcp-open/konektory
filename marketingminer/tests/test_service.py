from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_marketingminer.app import build_definition
from connector_marketingminer.service import MarketingminerService

TOKEN = "synthetic-private-token-0123456789"


def context(**updates: Any) -> InvocationContext:
    values = {"api_token": TOKEN, "pii_key": "synthetic-pii-key-0123456789abcdef"}
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="marketingminer/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_all_tools_use_documented_paths_and_query_token() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        data = [{"keyword": "Private phrase", "search_volume": 5}]
        return httpx.Response(200, json={"status": "success", "data": data})

    definition = build_definition(MarketingminerService(transport=httpx.MockTransport(upstream)))
    cases = {
        "keyword_search_volume": ({"keyword": "seo", "lang": "sk"}, "/keywords/search-volume-data"),
        "keyword_suggestions": (
            {"keyword": "seo", "suggestions_type": "questions", "with_keyword_data": True},
            "/keywords/suggestions",
        ),
        "website_stats": (
            {"target": "example.test", "target_type": "subdomain"},
            "/websites/stats",
        ),
        "website_stats_range": (
            {"target": "https://example.test/blog", "target_type": "prefix", "period": "monthly"},
            "/websites/stats-range",
        ),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].method == "GET" and seen[-1].url.path == path
        assert seen[-1].url.params["api_token"] == TOKEN
        assert "authorization" not in seen[-1].headers
        assert seen[-1].headers["user-agent"].startswith("OpenMCP/")
        serialized = result.model_dump_json()
        assert "Private phrase" not in serialized and TOKEN not in serialized
    assert seen[0].url.params["lang"] == "sk" and seen[0].url.params["keyword"] == "seo"
    assert seen[1].url.params["suggestions_type"] == "questions"
    assert seen[1].url.params["with_keyword_data"] == "true"
    assert seen[2].url.params["type"] == "subdomain" and seen[2].url.params["lang"] == "cs"
    assert seen[3].url.params["period"] == "monthly"
    assert "scheme" not in seen[3].url.params


@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "success", "data": []})

    service = MarketingminerService(transport=httpx.MockTransport(upstream))
    for bad in ({"api_token": "short"}, {"api_token": "bad token\n"}, {"api_token": ""}):
        with pytest.raises(ConnectorError) as caught:
            await service.test_connection(context(credentials=bad))
        assert caught.value.code is ErrorCode.CREDENTIAL_INVALID
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(secret_ref="marketingminer/other/inst-1"))
    assert caught.value.code is ErrorCode.FORBIDDEN
    assert calls == 0
    assert await service.test_connection(context()) == {"connected": True}
    assert calls == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"status": "error", "message": "private detail"}),
        httpx.Response(200, json={"status": "fail", "data": {"keyword": "private detail"}}),
        httpx.Response(200, json={"status": "success"}),
        httpx.Response(200, json=["private detail"]),
        httpx.Response(200, content=b"<html>private detail</html>"),
        httpx.Response(401, json={"message": "private detail"}),
        httpx.Response(402, json={"message": "private detail"}),
        httpx.Response(429, json={"message": "private detail"}),
        httpx.Response(500, text="private detail"),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak(response: httpx.Response) -> None:
    service = MarketingminerService(transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert "private detail" not in str(caught.value)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("keyword_search_volume", {"keyword": "a"}),
        ("keyword_search_volume", {"keyword": "x" * 81}),
        ("keyword_search_volume", {"keyword": "seo", "lang": "de"}),
        ("keyword_search_volume", {"keyword": "seo", "api_token": "other"}),
        ("keyword_search_volume", {"keyword": 5}),
        ("keyword_suggestions", {"keyword": "seo", "with_keyword_data": "true"}),
        ("keyword_suggestions", {"keyword": "seo", "suggestions_type": "all"}),
        ("website_stats", {"target": ""}),
        ("website_stats", {"target": "bad host"}),
        ("website_stats", {"target": "example.test", "target_type": "url"}),
        ("website_stats_range", {"target": "example.test", "period": "yearly"}),
        ("website_stats_range", []),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
