from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_raynet.app import build_definition
from connector_raynet.service import RaynetService

RESOURCES = {
    "company",
    "person",
    "lead",
    "businessCase",
    "offer",
    "salesOrder",
    "project",
    "product",
}
CODEBOOKS = {
    "companyCategory",
    "personCategory",
    "businessCaseCategory",
    "businessCasePhase",
    "businessCaseType",
    "leadCategory",
    "leadPhase",
    "currency",
    "taxRate",
    "productCategory",
    "productLine",
    "offerCategory",
    "offerStatus",
    "salesOrderCategory",
    "salesOrderStatus",
    "projectStatus",
}


def context(**changes: Any) -> InvocationContext:
    values = {
        "instance_name": "synthetic-instance",
        "username": "synthetic-user",
        "api_key": "synthetic-api-key",
        "pii_key": "synthetic-pii-key-0123456789abcdef01234567",
    }
    values.update(changes.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="subject-1",
        workspace_id="workspace-1",
        installation_id="install-1",
        secret_ref="raynet/workspace-1/install-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=changes)


def invoke(
    name: str,
    arguments: dict[str, Any],
    payload: Any,
    *,
    ctx: InvocationContext | None = None,
    status: int = 200,
) -> tuple[Any, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status, json=payload, headers={"Location": "https://outside.invalid/"}
        )

    service = RaynetService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools[name]
    result = asyncio.run(spec.handler(spec.input_model.model_validate(arguments), ctx or context()))
    return result, seen


ROUTES = [
    ("raynet_whoami", {}, "/security/info"),
    ("search_companies", {}, "/company/"),
    ("get_company", {"company_id": 7}, "/company/7/"),
    ("get_company_by_ext", {"ext_id": "EXT-7"}, "/company/ext/EXT-7/"),
    ("search_persons", {}, "/person/"),
    ("get_person", {"person_id": 7}, "/person/7/"),
    ("get_person_by_ext", {"ext_id": "EXT-7"}, "/person/ext/EXT-7/"),
    ("search_business_cases", {}, "/businessCase/"),
    ("get_business_case", {"business_case_id": 7}, "/businessCase/7/"),
    ("search_leads", {}, "/lead/"),
    ("get_lead", {"lead_id": 7}, "/lead/7/"),
    ("raynet_list", {"resource": "offer"}, "/offer/"),
    ("raynet_get", {"resource": "product", "record_id": 7}, "/product/7/"),
    ("raynet_get_by_ext", {"resource": "project", "ext_id": "EXT-7"}, "/project/ext/EXT-7/"),
    ("raynet_codebook", {"name": "currency"}, "/currency/"),
]


@pytest.mark.parametrize("name,args,path", ROUTES)
def test_tool_routes_and_credential_protection(name: str, args: dict[str, Any], path: str) -> None:
    record = {
        "id": 7,
        "name": "Private Person",
        "email": "synthetic@example.test",
        "apiKey": "synthetic-api-key",
    }
    is_list = name.startswith("search_") or name in {"raynet_list", "raynet_codebook"}
    payload = {"success": True, "data": [record] if is_list else record, "totalCount": 1}
    result, seen = invoke(name, args, payload)
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.host == "app.raynet.cz"
    assert seen[0].url.path == "/api/v2" + path
    assert seen[0].headers["X-Instance-Name"] == "synthetic-instance"
    assert seen[0].headers["Authorization"].startswith("Basic ")
    for secret in (
        "Private Person",
        "synthetic@example.test",
        "synthetic-api-key",
        "synthetic-pii-key",
        "synthetic-user",
        "synthetic-instance",
    ):
        assert secret not in result.model_dump_json()
    assert result.provenance.source_url == "https://app.raynet.cz/api/v2"


def test_local_discovery_and_read_only_inventory() -> None:
    result, seen = invoke("raynet_resources", {}, None)
    assert seen == []
    assert set(result.data["resources"]) == RESOURCES
    assert set(result.data["codebooks"]) == CODEBOOKS
    definition = build_definition()
    assert set(definition.tools) == {name for name, _, _ in ROUTES} | {"raynet_resources"}
    assert all(tool.read_only for tool in definition.tools.values())


def test_first_class_and_generic_filters_keep_explicit_mapping() -> None:
    payload = {"success": True, "data": [], "totalCount": 0}
    _, seen = invoke("search_persons", {"company_id": 42, "firstName": "Synthetic"}, payload)
    assert seen[0].url.params["primaryRelationship-company-id"] == "42"
    assert seen[0].url.params["firstName"] == "Synthetic"
    assert "company_id" not in seen[0].url.params
    _, seen = invoke(
        "raynet_list",
        {"resource": "offer", "filters": {"company": 42}, "limit": 3, "offset": 8},
        payload,
    )
    assert seen[0].url.params["company"] == "42"
    assert seen[0].url.params["limit"] == "3"
    assert seen[0].url.params["offset"] == "8"


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_company", {"company_id": True}),
        ("get_company", {"company_id": "7"}),
        ("get_company", {"company_id": 0}),
        ("get_person", {"person_id": 2**53}),
        ("get_company_by_ext", {"ext_id": "../other"}),
        ("get_company_by_ext", {"ext_id": "x?url=https://outside.invalid"}),
        ("get_company_by_ext", {"ext_id": "x" * 129}),
        ("search_companies", {"limit": 101}),
        ("search_companies", {"limit": True}),
        ("search_companies", {"offset": -1}),
        ("search_companies", {"offset": 10001}),
        ("search_companies", {"url": "https://outside.invalid"}),
        ("raynet_get", {"resource": "userAccount", "record_id": 1}),
        ("raynet_list", {"resource": "email"}),
        ("raynet_list", {"resource": "company", "filters": {"url": "https://outside.invalid"}}),
        ("raynet_list", {"resource": "company", "filters": {"limit": 1000}}),
        ("raynet_list", {"resource": "product", "filters": {"contactInfo.email": "x"}}),
        ("raynet_list", {"resource": "company", "filters": {"name": {"nested": "value"}}}),
        ("raynet_codebook", {"name": "maritalStatus"}),
        ("raynet_codebook", {"name": "currency", "limit": 101}),
    ],
)
def test_bad_arguments_never_reach_provider(name: str, args: dict[str, Any]) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Invalid arguments must not reach the provider")

    spec = build_definition(RaynetService(transport=httpx.MockTransport(upstream))).tools[name]
    with pytest.raises((ValidationError, ConnectorError)):
        asyncio.run(spec.handler(spec.input_model.model_validate(args), context()))


@pytest.mark.parametrize(
    "changes",
    [
        {"secret_ref": "raynet/other/install-1"},
        {"secret_version": None},
        {"credentials": {"instance_name": "evil\r\nAuthorization: other"}},
        {"credentials": {"instance_name": "x" * 64}},
        {"credentials": {"instance_name": "instance/path"}},
        {"credentials": {"pii_key": "short"}},
        {"credentials": {"username": "bad:username"}},
    ],
)
def test_bad_credentials_have_no_external_effect(changes: dict[str, Any]) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Provider must not be called")

    service = RaynetService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        asyncio.run(service.test_connection(context(**changes)))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"success": False, "data": []},
        {"success": "true", "data": []},
        {"success": 1, "data": []},
        {"success": True},
        {"success": True, "data": None},
        {"success": True, "data": "secret-error"},
        {"success": True, "data": [None]},
    ],
)
def test_malformed_upstream_does_not_become_empty_success(payload: Any) -> None:
    with pytest.raises(ConnectorError) as caught:
        invoke("search_companies", {}, payload)
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR


def test_complete_page_and_outer_pagination_are_preserved() -> None:
    payload = {
        "success": True,
        "data": [{"id": index} for index in range(1, 101)],
        "totalCount": 101,
    }
    result, seen = invoke("search_companies", {"limit": 100}, payload)
    assert len(result.data["data"]) == 100
    assert result.data["data"][-1]["id"] == 100
    assert result.data["totalCount"] == 101
    assert seen[0].url.params["limit"] == "100"
    next_page, seen = invoke(
        "search_companies",
        {"limit": 100, "offset": 100},
        {"success": True, "data": [{"id": 101}], "totalCount": 101},
    )
    assert next_page.data["data"][0]["id"] == 101
    assert seen[0].url.params["offset"] == "100"


def test_overlarge_response_is_rejected_without_truncation() -> None:
    with pytest.raises(ConnectorError):
        invoke("search_companies", {}, {"success": True, "data": [{"name": "x" * (1024 * 1024)}]})


@pytest.mark.parametrize(
    "payload",
    [
        {"success": True, "data": [], "totalCount": True},
        {"success": True, "data": [], "totalCount": "1"},
        {"success": True, "data": [], "totalCount": -1},
        {"success": True, "data": [], "totalCount": 5},
        {"success": True, "data": [{"id": 7}], "totalCount": 0},
    ],
)
def test_invalid_pagination_is_not_success(payload: Any) -> None:
    with pytest.raises(ConnectorError):
        invoke("search_companies", {}, payload)


def test_provider_cannot_expand_requested_page_and_codebook_default_is_bounded() -> None:
    with pytest.raises(ConnectorError):
        invoke("search_companies", {"limit": 1}, {"success": True, "data": [{"id": 1}, {"id": 2}]})
    _, seen = invoke("raynet_codebook", {"name": "currency"}, {"success": True, "data": []})
    assert seen[0].url.params["limit"] == "100"


@pytest.mark.parametrize(
    "status,code",
    [
        (302, ErrorCode.UPSTREAM_ERROR),
        (401, ErrorCode.CREDENTIAL_INVALID),
        (404, ErrorCode.NOT_FOUND),
    ],
)
def test_safe_provider_status_and_no_redirect(status: int, code: ErrorCode) -> None:
    with pytest.raises(ConnectorError) as caught:
        invoke("search_companies", {}, {"error": "synthetic-api-key"}, status=status)
    assert caught.value.code is code
    assert "synthetic-api-key" not in str(caught.value)


def test_safe_connection_has_fixed_endpoint_and_pseudonyms_are_instance_bound() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True, "data": {"id": 7}})

    service = RaynetService(transport=httpx.MockTransport(upstream))
    assert asyncio.run(service.test_connection(context())) == {"connected": True}
    assert seen[0].url.path == "/api/v2/security/info"
    payload = {"success": True, "data": {"email": "synthetic@example.test"}}
    first, _ = invoke("get_company", {"company_id": 7}, payload)
    repeat, _ = invoke("get_company", {"company_id": 7}, payload)
    other, _ = invoke(
        "get_company",
        {"company_id": 7},
        payload,
        ctx=context(credentials={"instance_name": "other-instance"}),
    )
    assert first.data == repeat.data
    assert first.data != other.data
