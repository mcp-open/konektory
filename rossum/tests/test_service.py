from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_rossum import schemas as s
from connector_rossum.app import build_definition
from connector_rossum.service import SLUG, RossumService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
TOKEN = "synthetic-private-rossum-token"
PRIVATE = "Very private invoice text"
INVALID = ErrorCode.CREDENTIAL_INVALID
UPSTREAM = ErrorCode.UPSTREAM_ERROR


def context(**changes: Any) -> InvocationContext:
    values = {
        "base_url": "https://acme.rossum.app/api/v1",
        "api_token": TOKEN,
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


@pytest.mark.anyio
async def test_all_tools_use_documented_get_paths_with_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.host == "acme.rossum.app"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(
            200,
            json={
                "pagination": {"next": None, "previous": None, "total": 1},
                "results": [{"id": 5, "status": "to_review", "content": PRIVATE}],
                "content": [{"id": 1, "category": "section", "children": [{"value": PRIVATE}]}],
            },
        )

    definition = build_definition(RossumService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_workspaces": ({"name": "Main"}, "/api/v1/workspaces"),
        "list_queues": ({"workspace_id": 3, "page_size": 50}, "/api/v1/queues"),
        "get_queue": ({"queue_id": 8}, "/api/v1/queues/8"),
        "list_annotations": (
            {"queue_id": 8, "status": ["to_review", "confirmed", "to_review"], "cursor": "cD0x"},
            "/api/v1/annotations",
        ),
        "get_annotation": ({"annotation_id": 5}, "/api/v1/annotations/5"),
        "get_annotation_content": ({"annotation_id": 5}, "/api/v1/annotations/5/content"),
        "list_documents": ({"original_file_name": "inv.pdf"}, "/api/v1/documents"),
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
        assert result.provenance.source_url == "https://acme.rossum.app/api/v1"
    assert seen[0].url.params["name"] == "Main"
    assert seen[0].url.params["page_size"] == "20"
    assert seen[1].url.params["workspace"] == "3"
    assert "workspace_id" not in seen[1].url.params
    assert seen[1].url.params["page_size"] == "50"
    assert seen[3].url.params["queue"] == "8"
    assert seen[3].url.params["status"] == "to_review,confirmed"
    assert seen[3].url.params["cursor"] == "cD0x"
    assert seen[3].url.params["ordering"] == "-created_at"
    assert seen[6].url.params["original_file_name"] == "inv.pdf"
    assert all(request.method == "GET" for request in seen)


@pytest.mark.anyio
async def test_test_connection_reads_current_user() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 1, "email": "person@example.test"})

    service = RossumService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == "/api/v1/auth/user"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"secret_ref": f"{SLUG}/other/install-1"}, ErrorCode.FORBIDDEN),
        ({"secret_version": None}, INVALID),
        ({"provider_credential": None}, INVALID),
        ({"credentials": {"pii_key": "short"}}, INVALID),
        ({"credentials": {"api_token": ""}}, INVALID),
        ({"credentials": {"api_token": "bad token"}}, INVALID),
        ({"credentials": {"base_url": "http://acme.rossum.app/api/v1"}}, INVALID),
        ({"credentials": {"base_url": "https://acme.rossum.app"}}, INVALID),
        ({"credentials": {"base_url": "https://evil.example/api/v1"}}, INVALID),
        (
            {"credentials": {"base_url": "https://acme.rossum.app.evil.example/api/v1"}},
            INVALID,
        ),
        ({"credentials": {"base_url": "https://a.b.rossum.app/api/v1"}}, INVALID),
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

    service = RossumService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_queues"]
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
        (httpx.Response(401, json={"detail": "Invalid token secret."}), INVALID),
        (httpx.Response(403, json={"detail": "Forbidden secret."}), INVALID),
        (httpx.Response(404, json={"detail": "Not found."}), ErrorCode.NOT_FOUND),
        (httpx.Response(429, json={"detail": "Throttled."}), ErrorCode.RATE_LIMITED),
        (httpx.Response(503, text="secret upstream trace"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(302, headers={"Location": "https://evil.example/"}), UPSTREAM),
        (httpx.Response(200, content=b"not json secret"), UPSTREAM),
        (httpx.Response(200, json=[{"secret": 1}]), UPSTREAM),
        (httpx.Response(200, json={"detail": "secret envelope"}), UPSTREAM),
        (httpx.Response(200, content=b"{" + b"a" * (2 * 1024 * 1024)), UPSTREAM),
    ],
)
async def test_upstream_failures_are_safe(response: httpx.Response, code: ErrorCode) -> None:
    service = RossumService(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(ConnectorError) as caught:
        await service.get(context(), "/queues")
    assert caught.value.code is code
    assert "secret" not in str(caught.value).lower()


@pytest.mark.parametrize(
    "arguments",
    [
        {"page_size": 0},
        {"page_size": 101},
        {"page_size": "5"},
        {"page_size": True},
        {"cursor": ""},
        {"cursor": "x" * 1025},
        {"cursor": "bad\r\ncursor"},
        {"queue_id": 0},
        {"queue_id": "8"},
        {"status": []},
        {"status": ["unknown"]},
        {"status": "to_review"},
        {"search": ""},
        {"ordering": "id"},
        {"url": "https://evil.example"},
        {"page": 1},
        [],
        "text",
        None,
    ],
)
def test_annotation_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        s.AnnotationList.model_validate(arguments)
    assert s.AnnotationList.model_validate({}).page_size == 20


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.QueueID, {"queue_id": -1}),
        (s.QueueID, {}),
        (s.AnnotationID, {"annotation_id": 2**53}),
        (s.WorkspaceList, {"name": "a" * 129}),
        (s.QueueList, {"workspace_id": True}),
        (s.DocumentList, {"ordering": "mime_type"}),
    ],
)
def test_other_models_reject_bad_values(model: type[s.Input], arguments: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
