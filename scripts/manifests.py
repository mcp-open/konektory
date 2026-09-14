"""Canonical manifests for newly ported providers; check drift without network access."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS: dict[str, dict[str, Any]] = {
    "superfaktura": {
        "name": "SuperFaktúra",
        "category": "accounting",
        "summary": "Čtecí přístup k fakturám, klientům a nákladům SuperFaktúry.",
        "credentials": ["region", "email", "api_key", "pii_key"],
        "egress": {
            "hosts": [
                "moja.superfaktura.sk",
                "moje.superfaktura.cz",
                "sandbox.superfaktura.sk",
                "sandbox.superfaktura.cz",
            ],
            "port": 443,
        },
    },
    "fakturoid": {
        "name": "Fakturoid",
        "category": "accounting",
        "summary": "Čtecí přístup k dokladům, kontaktům a nákladům Fakturoidu.",
        "auth": {"type": "oauth_delegated", "provider": "fakturoid"},
        "credentials": ["account_slug", "access_token", "pii_key"],
        "egress": {"host": "app.fakturoid.cz", "port": 443, "path_prefix": "/api/v3"},
    },
    "freelo": {
        "name": "Freelo",
        "category": "project_management",
        "summary": "Čtecí přístup k projektům a úkolům Freelo.",
        "credentials": ["email", "api_key", "pii_key"],
        "egress": {"host": "api.freelo.io", "port": 443, "path_prefix": "/v1"},
    },
    "abraflexi": {
        "name": "ABRA Flexi",
        "category": "erp",
        "summary": "Čtecí přístup k evidencím ABRA Flexi Cloud.",
        "credentials": ["api_url", "username", "password", "company", "pii_key"],
        "egress": {"host_pattern": "{tenant}.flexibee.eu", "port": 5434, "path_prefix": "/c"},
    },
    "raynet": {
        "name": "RAYNET",
        "category": "crm",
        "summary": "Čtecí přístup k CRM RAYNET v českém regionu.",
        "credentials": ["instance_name", "username", "api_key", "pii_key"],
        "egress": {"host": "app.raynet.cz", "port": 443, "path_prefix": "/api/v2"},
    },
    "upgates": {
        "name": "Upgates",
        "category": "ecommerce",
        "summary": "Čtecí přístup k objednávkám, katalogu a nastavení e-shopu Upgates.",
        "credentials": ["api_url", "api_login", "api_key", "pii_key"],
        "egress": {
            "host_pattern": "{tenant}.admin.upgates.com",
            "port": 443,
            "path_prefix": "/api/v2",
        },
    },
}


def manifest(slug: str) -> dict[str, Any]:
    metadata = PROVIDERS[slug]
    definition = importlib.import_module(f"connector_{slug}.app").build_definition()
    return {
        "schema_version": 1,
        "slug": slug,
        "name": metadata["name"],
        "version": definition.version,
        "category": metadata["category"],
        "summary": metadata["summary"],
        "auth": metadata.get("auth", {"type": "api_key"})
        | {"credential_delivery": "core_signed_body_exact_version"},
        "credentials": [
            {"key": name, "required": True, "secret": True} for name in metadata["credentials"]
        ],
        "capabilities": {
            "default_read_only": True,
            "supports_write": False,
            "supports_test": True,
            "pii_pseudonymization": "configurable",
            "privacy_modes": ["strict", "balanced", "plain"],
            "default_privacy_mode": "strict",
        },
        "runtime": {
            "transport": "internal_http",
            "invocation_path": "/internal/v1/invoke",
            "test_connection_path": "/internal/v1/test-connection",
            "health_live_path": "/health/live",
            "health_ready_path": "/health/ready",
            "port_env": "OPENMCP_HTTP_ADDR",
            "internal_auth_env": "OPENMCP_INTERNAL_TOKEN",
            "internal_auth_file_env": "OPENMCP_INTERNAL_TOKEN_FILE",
            "internal_auth_source_policy": "exactly_one",
            "internal_auth": "hmac_sha256_compact_v1",
            "internal_auth_max_ttl_seconds": 60,
            "internal_auth_replay_store": "valkey_required_in_production",
        },
        "egress": metadata["egress"] | {"methods": ["GET"]},
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "read_only": tool.read_only,
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in definition.tools.values()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", dest="slug", choices=PROVIDERS)
    parser.add_argument("--write", dest="write_slug", choices=PROVIDERS)
    args = parser.parse_args()
    if args.slug:
        print(yaml.safe_dump(manifest(args.slug), allow_unicode=True, sort_keys=False), end="")
        return
    if args.write_slug:
        path = ROOT / args.write_slug / "connector.yaml"
        path.write_text(
            yaml.safe_dump(manifest(args.write_slug), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"Manifest written: {args.write_slug}")
        return
    for slug in PROVIDERS:
        actual = yaml.safe_load((ROOT / slug / "connector.yaml").read_text())
        if actual != manifest(slug):
            raise SystemExit(f"Manifest drift: {slug}")
        print(f"Manifest OK: {slug}")


if __name__ == "__main__":
    main()
