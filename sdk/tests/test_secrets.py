from __future__ import annotations

import json

import httpx
import pytest

from openmcp_connector_runtime import ConnectorError, ErrorCode, OpenBaoSecretResolver


@pytest.mark.anyio
async def test_openbao_uses_approle_and_reads_exact_kv_version(tmp_path) -> None:
    role_file = tmp_path / "role-id"
    secret_file = tmp_path / "secret-id"
    role_file.write_text("role-value\n")
    secret_file.write_text("secret-value\n")
    calls: list[str] = []

    async def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/auth/approle/login":
            assert json.loads(request.content) == {
                "role_id": "role-value",
                "secret_id": "secret-value",
            }
            return httpx.Response(
                200,
                json={
                    "auth": {
                        "client_token": "bao-client-token",
                        "lease_duration": 300,
                        "renewable": True,
                    }
                },
            )
        assert request.headers["X-Vault-Token"] == "bao-client-token"
        assert request.url.params["version"] == "7"
        assert request.url.path == (
            "/v1/secret/data/workspaces/ws-1/installations/inst-1/provider"
        )
        return httpx.Response(
            200,
            json={
                "data": {
                    "data": {
                        "refresh_token": "provider-secret",
                        "cloud_id": "cloud-1",
                        "pii_key": "long-pii-key",
                    },
                    "metadata": {"version": 7},
                }
            },
        )

    resolver = OpenBaoSecretResolver(
        "http://openbao:8200",
        role_file,
        secret_file,
        transport=httpx.MockTransport(responder),
    )
    try:
        first = await resolver.resolve("dotykacka/ws-1/inst-1", 7)
        second = await resolver.resolve("dotykacka/ws-1/inst-1", 7)
    finally:
        await resolver.close()
    assert first == second
    assert calls.count("/v1/auth/approle/login") == 1


@pytest.mark.anyio
async def test_openbao_rejects_unscoped_reference_before_network(tmp_path) -> None:
    role_file = tmp_path / "role-id"
    secret_file = tmp_path / "secret-id"
    role_file.write_text("role-value")
    secret_file.write_text("secret-value")
    resolver = OpenBaoSecretResolver(
        "http://openbao:8200",
        role_file,
        secret_file,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    try:
        with pytest.raises(ConnectorError) as error:
            await resolver.resolve("dotykacka/ws-1/../provider", 1)
    finally:
        await resolver.close()
    assert error.value.code == ErrorCode.FORBIDDEN


@pytest.mark.anyio
async def test_openbao_rejects_wrong_exact_version_metadata(tmp_path) -> None:
    role_file = tmp_path / "role-id"
    secret_file = tmp_path / "secret-id"
    role_file.write_text("role-value")
    secret_file.write_text("secret-value")

    async def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(
                200,
                json={"auth": {"client_token": "token", "lease_duration": 300}},
            )
        return httpx.Response(
            200,
            json={"data": {"data": {"value": "secret"}, "metadata": {"version": 8}}},
        )

    resolver = OpenBaoSecretResolver(
        "http://openbao:8200",
        role_file,
        secret_file,
        transport=httpx.MockTransport(responder),
    )
    try:
        with pytest.raises(ConnectorError) as error:
            await resolver.resolve("dotykacka/ws-1/inst-1", 7)
    finally:
        await resolver.close()
    assert error.value.code == ErrorCode.INTERNAL
