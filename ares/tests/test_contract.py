from __future__ import annotations

from pathlib import Path

import yaml
from starlette.testclient import TestClient

from connector_ares.app import build_definition, create_runtime_app


def test_manifest_matches_runtime_tools() -> None:
    definition = build_definition()
    manifest = yaml.safe_load((Path(__file__).parents[1] / "connector.yaml").read_text())
    assert manifest["slug"] == definition.slug
    assert manifest["version"] == definition.version
    assert {tool["name"] for tool in manifest["tools"]} == set(definition.tools)
    assert all(tool["read_only"] for tool in manifest["tools"])
    assert manifest["credentials"] == []
    assert manifest["runtime"]["internal_auth"] == "hmac_sha256_compact_v1"
    assert manifest["runtime"]["internal_auth_env"] == "OPENMCP_INTERNAL_TOKEN"
    assert manifest["runtime"]["internal_auth_file_env"] == "OPENMCP_INTERNAL_TOKEN_FILE"
    assert manifest["runtime"]["internal_auth_source_policy"] == "exactly_one"
    assert manifest["runtime"]["internal_auth_max_ttl_seconds"] == 60


def test_runtime_uses_shared_sdk_signing_key_file_contract(
    tmp_path: Path, monkeypatch
) -> None:
    signing_key = tmp_path / "ares-signing-key"
    signing_key.write_text("ares-private-signing-key-with-32-bytes\n", encoding="utf-8")
    monkeypatch.delenv("OPENMCP_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("OPENMCP_INTERNAL_TOKEN_FILE", str(signing_key))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPENMCP_REPLAY_STORE", "memory")

    with TestClient(create_runtime_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["connector"] == "ares"
