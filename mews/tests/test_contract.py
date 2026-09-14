from __future__ import annotations

from pathlib import Path

import yaml

from connector_mews.app import build_definition
from connector_mews.service import ALLOWED_PATHS, SLUG, VERSION


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
    # POST is the only verb the Connector API accepts; reads are limited to the exact
    # documented getAll/get operations enforced by the service allow-list.
    assert manifest["egress"] == {
        "hosts": ["api.mews.com", "api.mews-demo.com"],
        "port": 443,
        "path_prefix": "/api/connector/v1",
        "methods": ["POST"],
    }
    assert sorted(ALLOWED_PATHS) == [
        "/configuration/get",
        "/customers/getAll",
        "/enterprises/getAll",
        "/reservations/getAll/2023-06-06",
        "/resources/getAll",
        "/services/getAll",
    ]
    assert [item["key"] for item in manifest["credentials"]] == [
        "client_token",
        "access_token",
        "environment",
        "pii_key",
    ]
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
