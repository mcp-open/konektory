from __future__ import annotations

from pathlib import Path

import yaml
from starlette.testclient import TestClient

from connector_dotykacka.app import create_runtime_app


def test_manifest_publishes_only_mvp_tools_and_no_secret_values() -> None:
    manifest = yaml.safe_load((Path(__file__).parents[1] / "connector.yaml").read_text())
    assert [tool["name"] for tool in manifest["tools"]] == [
        "get_cloud_info",
        "list_orders",
        "sales_summary",
    ]
    assert manifest["credentials"] == []
    assert manifest["capabilities"]["supports_test"] is True
    assert manifest["runtime"]["test_connection_path"] == "/internal/v1/test-connection"
    assert manifest["runtime"]["internal_auth"] == "hmac_sha256_compact_v1"
    assert manifest["runtime"]["internal_auth_env"] == "OPENMCP_INTERNAL_TOKEN"
    assert manifest["runtime"]["internal_auth_file_env"] == "OPENMCP_INTERNAL_TOKEN_FILE"
    assert manifest["runtime"]["internal_auth_source_policy"] == "exactly_one"
    assert manifest["runtime"]["internal_auth_max_ttl_seconds"] == 60
    assert manifest["auth"]["credential_delivery"] == "core_signed_body_exact_version"
    assert "openbao_addr_env" not in manifest["runtime"]


def test_runtime_uses_shared_sdk_signing_key_file_contract(
    tmp_path: Path, monkeypatch
) -> None:
    signing_key = tmp_path / "dotykacka-signing-key"
    signing_key.write_text("dotykacka-private-signing-key-with-32-bytes\r\n", encoding="utf-8")
    monkeypatch.delenv("OPENMCP_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("OPENMCP_INTERNAL_TOKEN_FILE", str(signing_key))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPENMCP_REPLAY_STORE", "memory")

    with TestClient(create_runtime_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["connector"] == "dotykacka"
