from pathlib import Path

import yaml

from connector_marketingminer.app import build_definition


def test_manifest_matches_runtime() -> None:
    definition = build_definition()
    manifest = yaml.safe_load((Path(__file__).parents[1] / "connector.yaml").read_text())
    assert manifest["slug"] == definition.slug == "marketingminer"
    assert manifest["version"] == definition.version == "1.0.0"
    assert manifest["capabilities"]["supports_write"] is False
    assert manifest["tools"] == [
        {
            "name": tool.name,
            "description": tool.description,
            "read_only": True,
            "input_schema": tool.input_model.model_json_schema(),
        }
        for tool in definition.tools.values()
    ]
    assert manifest["egress"] == {
        "host": "profilers-api.marketingminer.com",
        "port": 443,
        "methods": ["GET"],
    }
    assert [item["key"] for item in manifest["credentials"]] == ["api_token", "pii_key"]
