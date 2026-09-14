from __future__ import annotations

from pathlib import Path

import yaml

from connector_supportbox.app import build_definition
from connector_supportbox.service import SLUG, VERSION


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
    assert manifest["runtime"]["internal_auth"] == "hmac_sha256_compact_v1"
    assert manifest["egress"] == {
        "host": "app.supportbox.cz",
        "port": 443,
        "path_prefix": "/api/rest/v2",
        "methods": ["GET"],
    }
    assert [item["key"] for item in manifest["credentials"]] == ["api_token", "pii_key"]
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
