"""Every registered adapter inherits these baseline runtime and manifest checks.

Provider-specific signed invocation, pagination, credentials and PII acceptance
remain mandatory adapter tests; these checks do not claim provider readiness.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml
from openmcp_connector_runtime import InMemoryReplayStore, create_app
from scripts.connector_inventory import load_connectors
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("slug", load_connectors(ROOT))
def test_registered_adapter_manifest_and_private_http_boundary(slug: str) -> None:
    definition = importlib.import_module(f"connector_{slug}.app").build_definition()
    manifest = yaml.safe_load((ROOT / slug / "connector.yaml").read_text())
    assert manifest["slug"] == definition.slug == slug
    assert manifest["version"] == definition.version
    names = [tool["name"] for tool in manifest["tools"]]
    assert len(names) == len(set(names))
    assert set(names) == set(definition.tools)
    assert names, "registered connector must declare a tested tool contract"
    assert all(tool["read_only"] is True for tool in manifest["tools"])
    assert all(tool.read_only is True for tool in definition.tools.values())
    assert manifest["capabilities"]["supports_write"] is False
    assert manifest["capabilities"]["default_read_only"] is True
    if definition.requires_secret:
        assert manifest["capabilities"]["pii_pseudonymization"] == "configurable"
        assert manifest["capabilities"]["privacy_modes"] == ["strict", "balanced", "plain"]
        assert manifest["capabilities"]["default_privacy_mode"] == "strict"
    assert manifest["capabilities"]["supports_test"] == (definition.test_connection is not None)
    runtime = manifest["runtime"]
    assert runtime["transport"] == "internal_http"
    assert runtime["internal_auth"] == "hmac_sha256_compact_v1"
    assert runtime["internal_auth_source_policy"] == "exactly_one"
    assert runtime["internal_auth_max_ttl_seconds"] == 60
    app = create_app(definition, signing_key="synthetic-shared-contract-key-0123456789abcdef",
                     replay_store=InMemoryReplayStore())
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        version = client.get("/version")
        assert version.status_code == 200
        assert version.json()["connector"] == slug
        for path in ("/internal/v1/invoke", "/internal/v1/test-connection"):
            assert client.post(path, json={}).status_code == 401
        assert client.post("/mcp", json={}).status_code == 404


# ARES and Dotykačka keep hand-written manifests; the platform compiles their
# Go definitions instead of these schemas.
_HAND_WRITTEN_MANIFESTS = frozenset({"ares", "dotykacka"})

# Go's RE2 rejects a repeat whose nested product exceeds 1000, and the platform
# compiles every published pattern with Go ``regexp`` at core startup.
_RE2_MAX_REPEAT = 1000
_BOUNDED = re.compile(r"\{(\d+)(?:,(\d+))?\}")


def _nested_repeat_product(pattern: str) -> int:
    worst = 1
    depth: list[int] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth.append(index)
        elif char == ")" and depth:
            start = depth.pop()
            quantifier = _BOUNDED.match(pattern, index + 1)
            if quantifier:
                outer = int(quantifier.group(2) or quantifier.group(1))
                inner = [
                    int(m.group(2) or m.group(1)) for m in _BOUNDED.finditer(pattern, start, index)
                ]
                worst = max(worst, outer * max(inner, default=1))
        index += 1
    return worst


def _patterns(schema: object) -> list[str]:
    if isinstance(schema, dict):
        found = [schema["pattern"]] if isinstance(schema.get("pattern"), str) else []
        return found + [p for value in schema.values() for p in _patterns(value)]
    if isinstance(schema, list):
        return [p for value in schema for p in _patterns(value)]
    return []


@pytest.mark.parametrize("slug", sorted(set(load_connectors(ROOT)) - _HAND_WRITTEN_MANIFESTS))
def test_manifest_input_schemas_match_models_and_compile_under_re2(slug: str) -> None:
    definition = importlib.import_module(f"connector_{slug}.app").build_definition()
    manifest = yaml.safe_load((ROOT / slug / "connector.yaml").read_text())
    for tool in manifest["tools"]:
        schema = definition.tools[tool["name"]].input_model.model_json_schema()
        assert tool["input_schema"] == schema, f"{slug}.{tool['name']} manifest drifted"
        for pattern in _patterns(schema):
            assert _nested_repeat_product(pattern) <= _RE2_MAX_REPEAT, (
                f"{slug}.{tool['name']}: pattern {pattern!r} exceeds Go RE2 repeat limits"
            )
