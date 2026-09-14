from __future__ import annotations

from pathlib import Path

import yaml

from connector_vyfakturuj.app import build_definition
from connector_vyfakturuj.service import ORIGIN, SLUG, VERSION


def test_manifest_matches_runtime() -> None:
    definition = build_definition()
    manifest = yaml.safe_load(
        (Path(__file__).parents[1] / "connector.yaml").read_text(encoding="utf-8")
    )
    assert manifest["slug"] == definition.slug == SLUG
    assert manifest["version"] == definition.version == VERSION == "1.0.0"
    assert manifest["capabilities"] == {
        "default_read_only": True,
        "supports_write": False,
        "supports_test": definition.test_connection is not None,
        "pii_pseudonymization": "configurable",
        "privacy_modes": ["strict", "balanced", "plain"],
        "default_privacy_mode": "strict",
    }
    runtime = manifest["runtime"]
    assert runtime["transport"] == "internal_http"
    assert runtime["internal_auth"] == "hmac_sha256_compact_v1"
    assert runtime["internal_auth_source_policy"] == "exactly_one"
    assert runtime["internal_auth_max_ttl_seconds"] == 60
    assert ORIGIN == "https://api.vyfakturuj.cz/2.0"
    assert manifest["egress"] == {
        "host": "api.vyfakturuj.cz",
        "port": 443,
        "path_prefix": "/2.0",
        "methods": ["GET"],
    }
    assert [item["key"] for item in manifest["credentials"]] == ["email", "api_key", "pii_key"]
    assert all(item["required"] and item["secret"] for item in manifest["credentials"])
    assert manifest["tools"] == [
        {
            "name": tool.name,
            "description": tool.description,
            "read_only": tool.read_only,
            "input_schema": tool.input_model.model_json_schema(),
        }
        for tool in definition.tools.values()
    ]
    assert all(tool.read_only for tool in definition.tools.values())
    assert len(definition.tools) == 8
