from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from openmcp_connector_runtime import ConnectorError, InvocationContext
from openmcp_connector_runtime.provider import (
    basic_auth,
    credentials,
    private_envelope,
    segment,
    validated_origin,
)


def context(**updates: object) -> InvocationContext:
    values = {
        "request_id": "req",
        "subject": "user",
        "workspace_id": "ws",
        "installation_id": "inst",
        "secret_ref": "upgates/ws/inst",
        "secret_version": 1,
        "provider_credential": {
            "api_key": SecretStr("synthetic-secret"),
            "pii_key": SecretStr("synthetic-pii-key-long-enough-12345"),
        },
    }
    return InvocationContext.model_validate(values | updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"secret_ref": "upgates/other/inst"},
        {"secret_version": None},
        {"provider_credential": None},
        {"provider_credential": {"api_key": "k", "pii_key": "short"}},
    ],
)
def test_credentials_fail_before_provider(updates: dict[str, object]) -> None:
    with pytest.raises(ConnectorError):
        credentials(context(**updates), "upgates", ("api_key",))


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.admin.upgates.com/api/v2",
        "https://evil.example/api/v2",
        "https://shop.admin.upgates.com.evil.example/api/v2",
        "https://admin.upgates.com/api/v2",
        "https://x.y.admin.upgates.com/api/v2",
        "https://u:p@shop.admin.upgates.com/api/v2",
        "https://shop.admin.upgates.com:444/api/v2",
        "https://shop.admin.upgates.com/api/v2?x=y",
        "https://shop.admin.upgates.com/api/v2#x",
        "https://shop.admin.upgates.com/api/v2/other",
        "https://shop.admin.upgates.com:/api/v2",
        "https://-shop.admin.upgates.com/api/v2",
        "https://shop.admin.upgates.com\n/api/v2",
        "https://127.0.0.1/api/v2",
        "https://shop.admin.upgates.com./api/v2",
    ],
)
def test_origin_rejects_untrusted_target(url: str) -> None:
    with pytest.raises(ConnectorError):
        validated_origin(url, suffix="admin.upgates.com", path="/api/v2")


def test_allowed_origin_and_basic() -> None:
    assert validated_origin(
        "https://SHOP.admin.upgates.com:443/api/v2/", suffix="admin.upgates.com", path="/api/v2"
    ) == ("https://shop.admin.upgates.com/api/v2")
    assert validated_origin("https://shop.flexibee.eu:5434", suffix="flexibee.eu", port=5434)
    assert basic_auth("user", "pass") == "Basic dXNlcjpwYXNz"
    for username, password in [("a:b", "p"), ("u", "p\n"), ("", "p")]:
        with pytest.raises(ConnectorError):
            basic_auth(username, password)


@pytest.mark.parametrize("value", ["../a", "a/b", "a?b", "a#b", "%2f", "..", "", True])
def test_path_segments_reject_traversal(value: str | bool) -> None:
    with pytest.raises(ConnectorError):
        segment(value)


def test_pii_scope_and_unknown_fields_do_not_leak() -> None:
    ctx = context()
    payload = {
        "id": 7,
        "order_total": 100.5,
        "customer": {"id": 99, "email": "x@example.test"},
        "oddPersonalField": "Sensitive Name",
        "name": "synthetic-secret",
        "x@example.test": "arbitrary-key-content",
        "quantity": 10**400,
    }

    def render(current: InvocationContext, scope: str = "shop") -> str:
        result = private_envelope(
            payload, current, "upgates", scope, "k" * 32, "https://shop.admin.upgates.com/api/v2"
        )
        assert result.data["id"] == 7
        assert result.data["order_total"] == 100.5
        assert result.data["customer"]["id"] != 99
        return json.dumps(result.data)

    output = render(ctx)
    for secret in ["x@example.test", "Sensitive Name", "synthetic-secret", "arbitrary-key-content"]:
        assert secret not in output
    assert output == render(ctx)
    assert output != render(context(workspace_id="other"))
    assert output != render(context(installation_id="other"))
    assert output != render(context(subject="other"))
    assert output != render(ctx, "other-shop")


def test_response_complexity_is_bounded() -> None:
    with pytest.raises(ConnectorError):
        private_envelope([1] * 10_001, context(), "upgates", "shop", "k" * 32, "https://x.test")


def test_balanced_keeps_business_data_and_masks_pii_and_free_text() -> None:
    payload = {
        "status": "paid",
        "product": {"name": "Espresso", "sku": "ESP-1"},
        "customer": {"id": 99, "name": "Jana Nováková", "email": "jana@example.test"},
        "note": "Volejte +421 905 123 456",
        "reference": "objednavka-7",
    }
    result = private_envelope(
        payload,
        context(runtime_flags={"privacy_mode": "balanced"}),
        "upgates", "shop", "k" * 32, "https://x.test",
    )
    assert result.data["status"] == "paid"
    assert result.data["product"] == {"name": "Espresso", "sku": "ESP-1"}
    assert result.data["reference"] == "objednavka-7"
    rendered = result.model_dump_json()
    assert "Jana Nováková" not in rendered
    assert "jana@example.test" not in rendered
    assert "+421 905 123 456" not in rendered


def test_plain_keeps_payload_but_never_credentials() -> None:
    result = private_envelope(
        {
            "customer": {"name": "Jana Nováková"},
            "echo": "prefix synthetic-secret suffix",
            "key-synthetic-secret-suffix": "value",
        },
        context(runtime_flags={"privacy_mode": "plain"}),
        "upgates", "shop", "k" * 32, "https://x.test",
    )
    assert result.data["customer"]["name"] == "Jana Nováková"
    assert "synthetic-secret" not in result.model_dump_json()


@pytest.mark.parametrize("value", ["off", "", 1, True])
def test_invalid_privacy_mode_fails_closed(value: object) -> None:
    with pytest.raises(ConnectorError):
        private_envelope(
            {"id": 1}, context(runtime_flags={"privacy_mode": value}),
            "upgates", "shop", "k" * 32, "https://x.test",
        )


def test_decimal_metrics_keep_precision_but_private_unknown_and_credential_values_do_not() -> None:
    payload = {
        "sumCelkem": "123456789012345.12345678",
        "cenaMj": "12.3400",
        "sumCelkemMen": "42.0100",
        "@rowCount": "42",
        "total": "1e999",
        "owner": {"sumCelkem": "12.3400"},
        "unreviewed": "12.3400",
        "price": "synthetic-secret",
    }
    result = private_envelope(
        payload, context(), "abraflexi", "company", "k" * 32, "https://x.test"
    )
    assert result.data["sumCelkem"] == "123456789012345.12345678"
    assert result.data["cenaMj"] == "12.3400"
    assert result.data["sumCelkemMen"] == "42.0100"
    assert result.data["@rowCount"] == "42"
    assert result.data["owner"]["sumCelkem"] != "12.3400"
    assert "1e999" not in result.model_dump_json()
    assert "synthetic-secret" not in result.model_dump_json()
    assert "unreviewed" not in result.data


def test_balanced_keeps_dates_amounts_and_codes_but_masks_czech_pii() -> None:
    # Field names and value shapes as returned by a live ABRA Flexi invoice.
    payload = {
        "id": "2416",
        "kod": "FV1-000002/2025",
        "datVyst": "2025-01-01+01:00",
        "lastUpdate": "2026-01-02T15:03:58.737+01:00",
        "sumCelkem": "1355.0",
        "sumZklCelkem": "27099610.07",
        "varSym": "17578",
        "ic": "27074358",
        "idUcetniDenik": "2147498627",
        "stavUhrK": "stavUhr.uhrazeno",
        "firma": "code:NOVAK",
        "firma@showAs": "NOVAK: Jan Novák",
        "updatedBy@showAs": "Nováková Jana",
        "nazFirmy": "Jan Novák s.r.o.",
        "ulice": "Dlouhá 12",
        "mesto": "Praha",
        "psc": "170 00",
        "kontaktJmeno": "Jan Novák",
        "kontaktEmail": "jan@example.test",
        "kontaktTel": "+420774652283",
        "faNazev": "Výdejní místo Novák",
        "popis": "Konzultace pro Jana Nováka",
        "poznam": "Účet CZ65 0800 0000 1920 0014 5399",
        "uvodTxt": "Volejte 777 123 456 nebo 00420777123456",
        "polozkyFaktury": [{"id": "9", "nazev": "Konzultace", "cenaMj": "5000.00"}],
    }
    result = private_envelope(
        payload,
        context(runtime_flags={"privacy_mode": "balanced"}),
        "abraflexi", "company", "k" * 32, "https://x.test",
    )
    data = result.data
    for key in ("id", "kod", "datVyst", "lastUpdate", "sumCelkem", "sumZklCelkem", "varSym",
                "ic", "idUcetniDenik", "stavUhrK", "polozkyFaktury"):
        assert data[key] == payload[key], key
    rendered = result.model_dump_json()
    for leak in ("Novák", "Dlouhá", "Praha", "170 00", "jan@example.test", "774652283",
                 "777 123 456", "00420777123456", "1920 0014 5399", "Konzultace pro"):
        assert leak not in rendered, leak
    assert data["kontaktEmail"].startswith("<FIELD_")


def test_strict_keeps_zoned_dates_and_item_lists_addressable() -> None:
    payload = {
        "id": "1",
        "datVyst": "2025-01-01+01:00",
        "lastUpdate": "2026-01-02T15:03:58.737+01:00",
        "datUhr": "2025-13-40",
        "polozkyFaktury": [{"id": "9", "nazev": "Private"}],
    }
    result = private_envelope(payload, context(), "abraflexi", "company", "k" * 32, "https://x")
    assert result.data["datVyst"] == "2025-01-01+01:00"
    assert result.data["lastUpdate"] == "2026-01-02T15:03:58.737+01:00"
    assert result.data["datUhr"].startswith("<FIELD_")
    assert result.data["polozkyFaktury"][0]["id"] == "9"
    assert "Private" not in result.model_dump_json()


@pytest.mark.parametrize(
    "text,masked",
    [
        ("+420 777 123 456", True),
        ("777123456", True),
        ("00420777123456", True),
        ("(02) 1234 5678", True),
        ("2025-01-01", False),
        ("12100.00", False),
        ("27099610.07", False),
        ("27074358", False),
        ("2147498627", False),
        ("FV1-000002/2025", False),
        ("O17578", False),
    ],
)
def test_balanced_phone_heuristic(text: str, masked: bool) -> None:
    result = private_envelope(
        {"reference": text},
        context(runtime_flags={"privacy_mode": "balanced"}),
        "abraflexi", "company", "k" * 32, "https://x.test",
    )
    assert (text not in result.model_dump_json()) is masked


def test_adapter_declared_personal_fields_are_masked_in_balanced_mode() -> None:
    payload = {"items": [{"id": "1", "kod": "ADRIANA1", "nazev": "Adriana Nová", "stat": "CZ"}]}
    plain = private_envelope(
        payload, context(runtime_flags={"privacy_mode": "balanced"}),
        "abraflexi", "company", "k" * 32, "https://x.test",
    )
    assert plain.data["items"][0]["nazev"] == "Adriana Nová"
    guarded = private_envelope(
        payload, context(runtime_flags={"privacy_mode": "balanced"}),
        "abraflexi", "company", "k" * 32, "https://x.test",
        personal_fields=frozenset({"kod", "nazev"}),
    )
    row = guarded.data["items"][0]
    assert row["id"] == "1" and row["stat"] == "CZ"
    assert "Adriana" not in guarded.model_dump_json()
    assert "ADRIANA1" not in guarded.model_dump_json()


def test_balanced_masks_guest_identity_attributes_of_pms_profiles() -> None:
    # Field names of a Mews customer profile; identity attributes are personal.
    payload = {
        "Id": "4b428a36-5897-4585-9a58-b4b701433747",
        "Number": "1456",
        "NationalityCode": "AW",
        "Sex": "Female",
        "BirthDateUtc": "1999-03-28T12:00:00Z",
        "BirthPlace": "Pescara",
        "Occupation": "Giornalista",
        "TaxIdentificationNumber": "ZGNZLR17U72P554F",
        "CarRegistrationNumber": "AA 111AA",
        "Classifications": ["Returning"],
    }
    result = private_envelope(
        payload, context(runtime_flags={"privacy_mode": "balanced"}),
        "mews", "enterprise", "k" * 32, "https://x.test",
    )
    assert result.data["Id"] == payload["Id"]
    assert result.data["Number"] == "1456"
    assert result.data["Classifications"] == ["Returning"]
    rendered = result.model_dump_json()
    for leak in ("AW", "Female", "1999-03-28", "Pescara", "Giornalista", "ZGNZLR", "AA 111AA"):
        assert leak not in rendered, leak
