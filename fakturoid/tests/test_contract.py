from pathlib import Path

import yaml

from connector_fakturoid.app import build_definition


def test_manifest_matches_runtime() -> None:
    definition = build_definition()
    manifest = yaml.safe_load((Path(__file__).parents[1] / "connector.yaml").read_text())
    assert manifest["slug"] == definition.slug == "fakturoid"
    assert manifest["version"] == definition.version == "1.0.0"
    assert {tool["name"] for tool in manifest["tools"]} == set(definition.tools)
    assert all(tool["read_only"] for tool in manifest["tools"])
    assert manifest["auth"]["type"] == "oauth_delegated"
    assert manifest["egress"] == {
        "host": "app.fakturoid.cz",
        "port": 443,
        "path_prefix": "/api/v3",
        "methods": ["GET"],
    }
    assert [item["key"] for item in manifest["credentials"]] == [
        "account_slug",
        "access_token",
        "pii_key",
    ]
