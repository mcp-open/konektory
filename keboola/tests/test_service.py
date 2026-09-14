from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_keboola import schemas as s
from connector_keboola.app import build_definition
from connector_keboola.service import ORIGINS, SLUG, KeboolaService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
TOKEN = "1234-synthetic-private-storage-token"
PRIVATE = "Very private customer row"
INVALID = ErrorCode.CREDENTIAL_INVALID
UPSTREAM = ErrorCode.UPSTREAM_ERROR


def context(**changes: Any) -> InvocationContext:
    values = {"stack": "aws-eu-central-1", "storage_token": TOKEN, "pii_key": PII_KEY}
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


@pytest.mark.anyio
async def test_all_tools_use_documented_get_paths_with_storage_token() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.host == "connection.eu-central-1.keboola.com"
        assert request.headers["x-storageapi-token"] == TOKEN
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "id": "in.c-main.orders",
                "name": PRIVATE,
                "columns": ["id", "name"],
                "rows": [[{"columnName": "name", "value": PRIVATE, "isTruncated": False}]],
                "owner": {"id": 58, "name": PRIVATE},
            },
        )

    definition = build_definition(KeboolaService(transport=httpx.MockTransport(upstream)))
    cases = {
        "verify_token": ({}, "/v2/storage/tokens/verify"),
        "list_buckets": ({"include_metadata": True}, "/v2/storage/buckets"),
        "list_tables": ({"bucket_id": "in.c-main"}, "/v2/storage/buckets/in.c-main/tables"),
        "get_table": ({"table_id": "in.c-main.orders"}, "/v2/storage/tables/in.c-main.orders"),
        "preview_table": (
            {"table_id": "in.c-main.orders", "limit": 5, "columns": ["id", "name", "id"]},
            "/v2/storage/tables/in.c-main.orders/data-preview",
        ),
        "list_components": ({"component_type": "extractor"}, "/v2/storage/components"),
        "list_configurations": (
            {"component_id": "keboola.ex-db-mysql"},
            "/v2/storage/components/keboola.ex-db-mysql/configs",
        ),
        "list_jobs": ({"limit": 10, "offset": 20}, "/v2/storage/jobs"),
    }
    assert set(definition.tools) == set(cases)
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        assert spec.read_only
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert seen[-1].url.path == path
        text = result.model_dump_json()
        assert PRIVATE not in text and TOKEN not in text and PII_KEY not in text
        assert result.content_origin == "untrusted_external_data_not_instructions"
        assert result.provenance.source_url == ORIGINS["aws-eu-central-1"] + "/v2/storage"
    assert seen[1].url.params["include"] == "metadata"
    assert seen[2].url.params["include"] == "metadata"
    assert seen[4].url.params["format"] == "json"
    assert seen[4].url.params["limit"] == "5"
    assert seen[4].url.params["columns"] == "id,name"
    assert seen[5].url.params["componentType"] == "extractor"
    assert seen[7].url.params["limit"] == "10" and seen[7].url.params["offset"] == "20"
    assert all(request.method == "GET" for request in seen)

    spec = definition.tools["list_tables"]
    await spec.handler(spec.input_model.model_validate({"include_columns": True}), context())
    assert seen[-1].url.path == "/v2/storage/tables"
    assert seen[-1].url.params["include"] == "columns,metadata"


@pytest.mark.anyio
async def test_every_stack_maps_to_its_fixed_origin() -> None:
    hosts: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={"id": "1"})

    service = KeboolaService(transport=httpx.MockTransport(upstream))
    for stack, origin in ORIGINS.items():
        assert await service.test_connection(context(credentials={"stack": stack})) == {
            "connected": True
        }
        assert origin == f"https://{hosts[-1]}"
    assert len(set(hosts)) == len(ORIGINS) == 5


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"secret_ref": f"{SLUG}/other/install-1"}, ErrorCode.FORBIDDEN),
        ({"secret_version": None}, INVALID),
        ({"provider_credential": None}, INVALID),
        ({"credentials": {"pii_key": "short"}}, INVALID),
        ({"credentials": {"storage_token": ""}}, INVALID),
        ({"credentials": {"storage_token": "bad token"}}, INVALID),
        ({"credentials": {"stack": "https://connection.keboola.com"}}, INVALID),
        ({"credentials": {"stack": "evil.example"}}, INVALID),
        ({"credentials": {"stack": ""}}, INVALID),
    ],
)
async def test_bad_credentials_fail_closed_before_any_request(
    changes: dict[str, Any], code: ErrorCode
) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = KeboolaService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_buckets"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context(**changes))
    assert caught.value.code is code
    with pytest.raises(ConnectorError):
        await service.test_connection(context(**changes))
    assert calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(401, json={"error": "Invalid secret token"}), INVALID),
        (httpx.Response(403, json={"error": "Forbidden secret"}), INVALID),
        (httpx.Response(404, json={"error": "Not found secret"}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json={"error": "Too many secret"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(503, text="secret upstream trace"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(302, headers={"Location": "https://evil.example/"}), UPSTREAM),
        (httpx.Response(200, content=b"id,name\n1,secret\n"), UPSTREAM),
        (httpx.Response(200, json="secret"), UPSTREAM),
        (httpx.Response(200, json={"error": "secret", "exceptionId": "x"}), UPSTREAM),
        (httpx.Response(200, content=b"{" + b"a" * (2 * 1024 * 1024)), UPSTREAM),
    ],
)
async def test_upstream_failures_are_safe(response: httpx.Response, code: ErrorCode) -> None:
    service = KeboolaService(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(ConnectorError) as caught:
        await service.get(context(), "/buckets")
    assert caught.value.code is code
    assert "secret" not in str(caught.value).lower()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"table_id": ""},
        {"table_id": "in.c-main.orders", "limit": 0},
        {"table_id": "in.c-main.orders", "limit": 101},
        {"table_id": "in.c-main.orders", "limit": "5"},
        {"table_id": "in.c-main.orders", "limit": True},
        {"table_id": "in.c-main.orders", "columns": []},
        {"table_id": "in.c-main.orders", "columns": ["a,b"]},
        {"table_id": "in.c-main.orders", "columns": "id"},
        {"table_id": "../tokens"},
        {"table_id": "in.c-main.orders/events"},
        {"table_id": "in.c-main.orders", "whereColumn": "id"},
        {"table_id": "in.c-main.orders", "url": "https://evil.example"},
        {"table_id": "bad\r\nid"},
        [],
        "text",
        None,
    ],
)
def test_preview_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.TablePreview.model_validate(arguments)
    assert s.TablePreview.model_validate({"table_id": "in.c-main.orders"}).limit == 20


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.BucketList, {"include_metadata": "yes"}),
        (s.TableList, {"bucket_id": "in.c-main tables"}),
        (s.TableList, {"include_columns": 1}),
        (s.TableID, {"table_id": "x" * 129}),
        (s.ComponentList, {"component_type": "unknown"}),
        (s.ComponentID, {"component_id": "-bad"}),
        (s.JobList, {"limit": 101}),
        (s.JobList, {"offset": -1}),
        (s.JobList, {"offset": 10_001}),
        (s.Input, {"anything": 1}),
    ],
)
def test_other_models_reject_bad_values(model: type[s.Input], arguments: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
