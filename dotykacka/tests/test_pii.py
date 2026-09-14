from __future__ import annotations

import json
from pathlib import Path

from openmcp_connector_runtime import PrivacyMode

from connector_dotykacka.pii import Pseudonymizer

FIXTURES = Path(__file__).parent / "fixtures"


def test_orders_profile_masks_every_unknown_string_and_numeric_scalar() -> None:
    payload = json.loads((FIXTURES / "pii-aliases.json").read_text())

    result = Pseudonymizer(b"p" * 32).sanitize(payload, profile="orders")
    first = result["data"][0]

    assert first["newProviderField"].startswith("<FIELD_")
    assert first["newNumericField"].startswith("<FIELD_")
    assert first["newProviderField"] != "Alice Smith"
    assert first["newNumericField"] != 420123456
    assert first["totalValueRounded"] == 1234.5
    assert first["orderItems"][0]["quantity"] == 2


def test_orders_profile_masks_whole_free_text_fields() -> None:
    private_text = "Customer Alice Smith lives at Na Příkopě 1"
    payload = {
        "data": [
            {
                "note": private_text,
                "notes": private_text,
                "description": private_text,
                "text": private_text,
            }
        ]
    }

    result = Pseudonymizer(b"p" * 32).sanitize(payload, profile="orders")
    first = result["data"][0]

    assert all(first[field].startswith("<TEXT_") for field in payload["data"][0])
    assert private_text not in json.dumps(first, ensure_ascii=False)


def test_balanced_keeps_business_extensions_but_masks_person_data() -> None:
    payload = {
        "status": "paid",
        "providerBusinessCode": "B-17",
        "customer": {"name": "Jana Nováková", "email": "jana@example.test"},
        "note": "Zavolat zákazníkovi",
    }
    result = Pseudonymizer(b"p" * 32, mode=PrivacyMode.BALANCED).sanitize(payload)
    assert result["status"] == "paid"
    assert result["providerBusinessCode"] == "B-17"
    rendered = json.dumps(result)
    assert "Jana Nováková" not in rendered
    assert "jana@example.test" not in rendered
    assert "Zavolat zákazníkovi" not in rendered


def test_plain_keeps_provider_payload_but_masks_credentials() -> None:
    result = Pseudonymizer(
        b"p" * 32,
        mode=PrivacyMode.PLAIN,
        secret_values=frozenset({"provider-secret"}),
    ).sanitize(
        {
            "customer": {"name": "Jana Nováková"},
            "echo": "prefix provider-secret suffix",
            "key-provider-secret-suffix": "value",
        }
    )
    assert result["customer"]["name"] == "Jana Nováková"
    assert "provider-secret" not in str(result)
