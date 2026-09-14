from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_freelo.app import build_definition
from connector_freelo.service import FreeloService


def context(**updates: Any) -> InvocationContext:
    values = {
        "email": "synthetic@example.test",
        "api_key": "synthetic-private-key",
        "pii_key": "synthetic-pii-key-0123456789abcdef",
    }
    values.update(updates.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="user-1",
        workspace_id="ws-1",
        installation_id="inst-1",
        secret_ref="freelo/ws-1/inst-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=updates)


@pytest.mark.anyio
async def test_all_tools_use_documented_read_paths_and_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 7, "name": "Private project"})

    definition = build_definition(FreeloService(transport=httpx.MockTransport(upstream)))
    cases = {
        "list_projects": ({}, "/v1/projects"),
        "get_project": ({"project_id": 7}, "/v1/project/7"),
        "list_tasks": ({"page": 2, "project_id": 7, "search": "audit"}, "/v1/all-tasks"),
        "get_task": ({"task_id": 7}, "/v1/task/7"),
    }
    for name, (arguments, path) in cases.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert spec.read_only and seen[-1].url.path == path
        assert seen[-1].headers["authorization"].startswith("Basic ")
        assert seen[-1].headers["user-agent"].startswith("OpenMCP/")
        assert "Private project" not in result.model_dump_json()
    assert seen[2].url.params["p"] == "1"
    assert seen[2].url.params["projects_ids[]"] == "7"
    assert seen[3].url.params["comments_limit"] == "20"


@pytest.mark.anyio
async def test_bad_credentials_and_error_envelope_fail_closed() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"errors": [{"message": "private"}]})

    service = FreeloService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        await service.test_connection(context(credentials={"email": "bad:mail@example.test"}))
    assert calls == 0
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context())
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "arguments", [{"page": 0}, {"search": ""}, {"project_id": True}, {"url": "https://evil"}, []]
)
def test_arguments_are_closed_and_bounded(arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools["list_tasks"].input_model.model_validate(arguments)
