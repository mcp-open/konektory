from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_luigisbox import schemas as s
from connector_luigisbox.app import build_definition
from connector_luigisbox.service import (
    ALLOWED_POST_PATHS,
    SIGNED_CONTENT_TYPE,
    SLUG,
    LuigisboxService,
    signature,
)

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
TRACKER = "179075-204259"
PUBLIC_KEY = "179075-204259"
PRIVATE_KEY = "synthetic-private-key-0123456789"


def context(**changes: Any) -> InvocationContext:
    values = {
        "tracker_id": TRACKER,
        "public_key": PUBLIC_KEY,
        "private_key": PRIVATE_KEY,
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


CASES: dict[str, tuple[dict[str, Any], str, str]] = {
    "search": (
        {
            "q": "harry potter",
            "size": 20,
            "page": 2,
            "filters": ["type:item", "brand:Acme"],
            "must_filters": ["availability:1"],
            "sort": "price:asc",
            "facets": "brand,category",
            "hit_fields": "title,price",
            "use_fixits": False,
        },
        "GET",
        "/search",
    ),
    "autocomplete": ({"q": "iph", "type": "item:6,category:3"}, "GET", "/autocomplete/v2"),
    "top_items": ({"type": "item:10", "hit_fields": "title"}, "GET", "/v1/top_items"),
    "trending_queries": ({}, "GET", "/v2/trending_queries"),
    "recommend": (
        {
            "recommendation_type": "item_detail_alternatives",
            "item_ids": ["/products/123"],
            "size": 4,
            "hit_fields": ["title", "price"],
            "recommender_client_identifier": "product_detail",
        },
        "POST",
        "/v1/recommend",
    ),
    "content_export": (
        {"size": 50, "hit_fields": "title,web_url", "requested_types": "item,category"},
        "GET",
        "/v1/content_export",
    ),
}


def upstream_payload(request: httpx.Request) -> Any:
    if request.url.path == "/v1/recommend":
        hits = [{"url": "/p/1", "attributes": {"title": "Private product"}}]
        return [{"recommendation_type": "x", "hits": hits}]
    if request.url.path == "/v2/trending_queries":
        return [{"title": "Private product"}]
    return {"results": {"total_hits": 1, "hits": [{"attributes": {"title": "Private product"}}]}}


@pytest.mark.anyio
async def test_all_tools_use_documented_paths_tracker_and_hmac() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=upstream_payload(request))

    definition = build_definition(LuigisboxService(transport=httpx.MockTransport(upstream)))
    for name, (arguments, method, path) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == method
        assert request.url.host == "live.luigisbox.com" and request.url.path == path
        assert request.url.params["tracker_id"] == TRACKER
        assert request.headers["user-agent"].startswith("OpenMCP/")
        if method == "POST":
            assert path in ALLOWED_POST_PATHS
        if path == "/v1/content_export":
            date = request.headers["date"]
            expected = signature(PRIVATE_KEY, "GET", SIGNED_CONTENT_TYPE, date, path)
            assert request.headers["authorization"] == f"ApiAuth {PUBLIC_KEY}:{expected}"
            assert request.headers["content-type"] == SIGNED_CONTENT_TYPE
        else:
            assert "authorization" not in request.headers
        text = result.model_dump_json()
        assert PRIVATE_KEY not in text and PII_KEY not in text
        assert "Private product" not in text
    search = seen[0].url.params
    assert search["q"] == "harry potter" and search["size"] == "20" and search["page"] == "2"
    assert search.get_list("f[]") == ["type:item", "brand:Acme"]
    assert search.get_list("f_must[]") == ["availability:1"]
    assert search["sort"] == "price:asc" and search["use_fixits"] == "false"
    assert search["facets"] == "brand,category" and search["hit_fields"] == "title,price"
    assert seen[1].url.params["type"] == "item:6,category:3" and seen[1].url.params["q"] == "iph"
    assert seen[2].url.params["type"] == "item:10" and seen[2].url.params["hit_fields"] == "title"
    assert json.loads(seen[4].content) == [
        {
            "recommendation_type": "item_detail_alternatives",
            "item_ids": ["/products/123"],
            "size": 4,
            "hit_fields": ["title", "price"],
            "recommender_client_identifier": "product_detail",
        }
    ]
    assert seen[4].headers["content-type"] == "application/json"
    export = seen[5].url.params
    assert export["size"] == "50" and export["requested_types"] == "item,category"


def test_signature_matches_documented_formula() -> None:
    date = "Thu, 29 Jun 2017 12:11:16 GMT"
    data = f"GET\n{SIGNED_CONTENT_TYPE}\n{date}\n/v1/content_export"
    expected = base64.b64encode(
        hmac.new(b"secret", data.encode(), hashlib.sha256).digest()
    ).decode()
    assert signature("secret", "GET", SIGNED_CONTENT_TYPE, date, "/v1/content_export") == expected


@pytest.mark.anyio
async def test_test_connection_checks_tracker_and_signed_export() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=upstream_payload(request))

    service = LuigisboxService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert [request.url.path for request in seen] == ["/v2/trending_queries", "/v1/content_export"]
    assert seen[1].url.params["size"] == "1"
    assert seen[1].headers["authorization"].startswith(f"ApiAuth {PUBLIC_KEY}:")


@pytest.mark.anyio
async def test_post_is_refused_outside_the_allow_list_before_egress() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = LuigisboxService(transport=httpx.MockTransport(upstream))
    for path in ("/v1/content", "/v1/update_by_query", "/v1/recommend/create"):
        with pytest.raises(ConnectorError) as caught:
            await service._request(context(), "POST", path, json_body={"blocks": []})
        assert caught.value.code is ErrorCode.INTERNAL
    for method in ("PATCH", "DELETE", "PUT"):
        with pytest.raises(ConnectorError):
            await service._request(context(), method, "/v1/content")
    assert calls == 0


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"secret_ref": f"{SLUG}/other/install-1"}, ErrorCode.FORBIDDEN),
        ({"secret_version": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": None}, ErrorCode.CREDENTIAL_INVALID),
        ({"provider_credential": {}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"pii_key": "short"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"tracker_id": "bad tracker"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"public_key": "x/y"}}, ErrorCode.CREDENTIAL_INVALID),
        ({"credentials": {"private_key": "line\nbreak"}}, ErrorCode.CREDENTIAL_INVALID),
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
        return httpx.Response(200, json={})

    service = LuigisboxService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["trending_queries"]
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
        (httpx.Response(200, json={"error": "private detail"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"errors": ["private detail"]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json="private detail"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, content=b"<html>private detail</html>"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(400, json={"message": "private detail"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json={"message": "private detail"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, json={"message": "private detail"}), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(404, json={"message": "private detail"}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json={"message": "private detail"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(503, text="private detail"), ErrorCode.UPSTREAM_UNAVAILABLE),
    ],
)
@pytest.mark.anyio
async def test_provider_failures_never_leak_provider_text(
    response: httpx.Response, code: ErrorCode
) -> None:
    service = LuigisboxService(transport=httpx.MockTransport(lambda request: response))
    spec = build_definition(service).tools["search"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"q": "x"}), context())
    assert caught.value.code is code
    assert "private detail" not in str(caught.value)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"q": ""},
        {"q": "x", "size": 0},
        {"q": "x", "size": 201},
        {"q": "x", "page": 0},
        {"q": "x", "size": "5"},
        {"q": "x", "filters": ["no-colon"]},
        {"q": "x", "filters": ["a:b\n"]},
        {"q": "x", "filters": "type:item"},
        {"q": "x", "sort": "price:up"},
        {"q": "x", "facets": "brand category"},
        {"q": "x", "use_fixits": "true"},
        {"q": "x", "url": "https://outside.invalid"},
        {"q": "x", "tracker_id": "other"},
        [],
        "text",
        None,
    ],
)
def test_search_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.Search.model_validate(arguments)
    assert s.Search.model_validate({"filters": ["type:item"]}).q is None


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.Autocomplete, {}),
        (s.Autocomplete, {"q": "a", "type": "item"}),
        (s.Autocomplete, {"q": "a", "type": "item:0"}),
        (s.Autocomplete, {"q": "a", "type": "item:51"}),
        (s.Autocomplete, {"q": "a", "type": "Item:5"}),
        (s.TopItems, {"type": "item:5", "q": "x"}),
        (s.TrendingQueries, {"ctx": "a"}),
        (s.Recommend, {}),
        (s.Recommend, {"recommendation_type": "Best Sellers"}),
        (s.Recommend, {"recommendation_type": "bestsellers", "item_ids": []}),
        (s.Recommend, {"recommendation_type": "bestsellers", "item_ids": ["a"] * 11}),
        (s.Recommend, {"recommendation_type": "bestsellers", "item_ids": ["bad\nid"]}),
        (s.Recommend, {"recommendation_type": "bestsellers", "size": 51}),
        (s.Recommend, {"recommendation_type": "bestsellers", "hit_fields": "title"}),
        (s.ContentExport, {"size": 501}),
        (s.ContentExport, {"size": 0}),
        (s.ContentExport, {"requested_types": "item;drop"}),
    ],
)
def test_other_arguments_are_closed_and_bounded(
    model: type[s.Input], arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
