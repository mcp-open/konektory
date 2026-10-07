from pathlib import Path

import yaml

from connector_dokladbot.app import build_definition


def test_manifest_matches_runtime() -> None:
    definition = build_definition()
    manifest = yaml.safe_load((Path(__file__).parents[1] / "connector.yaml").read_text())
    assert manifest["slug"] == definition.slug == "dokladbot"
    assert manifest["version"] == definition.version == "1.0.0"
    assert {tool["name"] for tool in manifest["tools"]} == set(definition.tools)
    assert all(tool["read_only"] for tool in manifest["tools"])
    assert manifest["auth"]["type"] == "api_key"
    assert manifest["capabilities"]["supports_write"] is False
    assert manifest["egress"] == {
        "host": "dokladbot.cz",
        "port": 443,
        "path_prefix": "/api",
        "methods": ["GET"],
    }
    assert [item["key"] for item in manifest["credentials"]] == ["api_key", "pii_key"]
    assert manifest["tools"] == [
        {
            "name": tool.name,
            "description": tool.description,
            "read_only": tool.read_only,
            "input_schema": tool.input_model.model_json_schema(),
        }
        for tool in definition.tools.values()
    ]
