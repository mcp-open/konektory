from pathlib import Path

import yaml

from connector_freelo.app import build_definition


def test_manifest_matches_runtime() -> None:
    definition = build_definition()
    manifest = yaml.safe_load((Path(__file__).parents[1] / "connector.yaml").read_text())
    assert manifest["slug"] == definition.slug == "freelo"
    assert manifest["version"] == definition.version == "1.0.0"
    assert {tool["name"] for tool in manifest["tools"]} == set(definition.tools)
    assert all(tool["read_only"] for tool in manifest["tools"])
    assert manifest["egress"] == {
        "host": "api.freelo.io",
        "port": 443,
        "path_prefix": "/v1",
        "methods": ["GET"],
    }
    assert [item["key"] for item in manifest["credentials"]] == [
        "email",
        "api_key",
        "pii_key",
    ]
