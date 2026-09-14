from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_abraflexi.app import build_definition
from connector_abraflexi.schemas import EVIDENCES
from connector_abraflexi.service import AbraFlexiService


def context(**changes: Any) -> InvocationContext:
    values = {
        "api_url": "https://demo.flexibee.eu:5434",
        "company": "demo",
        "username": "synthetic-user",
        "password": "synthetic-password",
        "pii_key": "synthetic-pii-key-0123456789abcdef01234567",
    }
    values.update(changes.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="subject-1",
        workspace_id="workspace-1",
        installation_id="install-1",
        secret_ref="abraflexi/workspace-1/install-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=changes)


def invoke(
    name: str,
    arguments: dict[str, Any],
    response: Any,
    *,
    ctx: InvocationContext | None = None,
    status: int = 200,
) -> tuple[Any, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status, json=response, headers={"Location": "https://outside.invalid/"}
        )

    service = AbraFlexiService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools[name]
    result = asyncio.run(spec.handler(spec.input_model.model_validate(arguments), ctx or context()))
    return result, seen


CASES = [
    ("list_records", {"evidence": "cenik"}, "cenik", "/c/demo/cenik.json"),
    ("list_issued_invoices", {}, "faktura-vydana", "/c/demo/faktura-vydana.json"),
    ("get_issued_invoice", {"invoice_id": "7"}, "faktura-vydana", "/c/demo/faktura-vydana/7.json"),
    ("list_invoice_types", {}, "typ-faktury-vydane", "/c/demo/typ-faktury-vydane.json"),
    (
        "get_invoice_journal",
        {"invoice_id": "7"},
        "ucetni-denik",
        "/c/demo/ucetni-denik/(idDokl=7).json",
    ),
    ("list_received_orders", {}, "objednavka-prijata", "/c/demo/objednavka-prijata.json"),
    (
        "get_received_order",
        {"order_id": "7"},
        "objednavka-prijata",
        "/c/demo/objednavka-prijata/7.json",
    ),
    (
        "list_stock_movements",
        {"date_from": "2026-01-01", "date_to": "2026-01-31"},
        "skladovy-pohyb",
        "/c/demo/skladovy-pohyb/(datVyst>='2026-01-01' and datVyst<='2026-01-31').json",
    ),
    ("get_stock_movement", {"movement_id": "7"}, "skladovy-pohyb", "/c/demo/skladovy-pohyb/7.json"),
    (
        "get_stock_status",
        {"date": "2026-01-01", "warehouse": "MAIN"},
        "stav-skladu-k-datu",
        "/c/demo/stav-skladu-k-datu.json",
    ),
    ("list_products", {}, "cenik", "/c/demo/cenik.json"),
    ("get_product", {"product_id": "7"}, "cenik", "/c/demo/cenik/7.json"),
    ("get_record", {"evidence": "cenik", "record_id": "7"}, "cenik", "/c/demo/cenik/7.json"),
]


@pytest.mark.parametrize("name,args,evidence,path", CASES)
def test_read_routes_and_private_output(
    name: str,
    args: dict[str, Any],
    evidence: str,
    path: str,
) -> None:
    row = {"id": "7", "email": "synthetic@example.test", "nazev": "Private Name"}
    result, seen = invoke(name, args, {"winstrom": {evidence: [row], "@rowCount": "1"}})
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == path
    assert seen[0].url.host == "demo.flexibee.eu"
    assert seen[0].url.port == 5434
    assert seen[0].headers["Authorization"].startswith("Basic ")
    assert "synthetic@example.test" not in result.model_dump_json()
    assert "Private Name" not in result.model_dump_json()
    assert "synthetic-password" not in result.model_dump_json()
    assert result.provenance.source_url == "https://www.flexibee.eu"


def test_company_catalog_and_metadata_routes() -> None:
    # The live company detail endpoint returns `company` as a single object
    # (https://demo.flexibee.eu:5434/c/demo.json); the server-wide listing
    # wraps it in a list. Both shapes must be accepted.
    live_shape = {"companies": {"company": {"dbNazev": "demo", "id": "-2", "nazev": "Private"}}}
    list_shape = {"companies": {"company": [{"id": "7", "name": "Private"}]}}
    for name in ("get_company_info", "list_companies"):
        for payload in (live_shape, list_shape):
            result, seen = invoke(name, {}, payload)
            assert seen[0].url.path == "/c/demo.json"
            assert "Private" not in result.model_dump_json()
    with pytest.raises(ConnectorError):
        invoke("get_company_info", {}, {"companies": {"company": "not-a-company"}})
    catalog, seen = invoke("list_evidences", {}, None)
    assert seen == []
    assert {item["evidence"] for item in catalog.data["items"]} == set(EVIDENCES)
    assert len(build_definition().tools) == 18
    assert all(tool.read_only for tool in build_definition().tools.values())
    result, seen = invoke(
        "sum_records", {"evidence": "faktura-vydana"}, {"winstrom": {"sumCelkem": "12345.60"}}
    )
    assert seen[0].url.path.endswith("/faktura-vydana/$sum.json")
    assert result.data["sumCelkem"] == "12345.60"
    result, seen = invoke(
        "get_evidence_properties",
        {"evidence": "cenik"},
        {
            "properties": {
                "evidenceName": "Ceník",
                "tagName": "cenik",
                "property": [{"propertyName": "id", "type": "integer", "isWritable": "false"}],
            }
        },
    )
    assert seen[0].url.path.endswith("/cenik/properties.json")
    assert result.data == {
        "evidence": "cenik",
        "fields": [{"name": "id", "type": "integer", "writable": False}],
    }


@pytest.mark.parametrize(
    "name,args",
    [
        ("list_records", {"evidence": "uzivatelsky-dotaz"}),
        ("list_records", {"evidence": "cenik", "raw_filter": "id=1"}),
        (
            "list_records",
            {"evidence": "cenik", "filters": [{"field": "id) or (1=1", "op": "eq", "value": 1}]},
        ),
        (
            "list_records",
            {
                "evidence": "cenik",
                "filters": [{"field": "nazev", "op": "eq", "value": "x') or (1=1"}],
            },
        ),
        (
            "list_records",
            {"evidence": "cenik", "filters": [{"field": "id", "op": "eq", "value": float("inf")}]},
        ),
        (
            "list_records",
            {"evidence": "cenik", "filters": [{"field": "id", "op": "eq", "value": 10**400}]},
        ),
        (
            "list_records",
            {"evidence": "cenik", "filters": [{"field": "id", "op": "is_null", "value": 1}]},
        ),
        ("list_records", {"evidence": "cenik", "order": "id@D&action=delete"}),
        ("list_records", {"evidence": "cenik", "fields": ["id);DROP"]}),
        ("list_products", {"limit": 101}),
        ("list_products", {"limit": True}),
        ("list_products", {"offset": -1}),
        ("list_products", {"url": "https://outside.invalid"}),
        ("get_record", {"evidence": "cenik", "record_id": "../firma"}),
        ("get_record", {"evidence": "cenik", "record_id": "7", "relations": ["prilohy"]}),
        ("list_issued_invoices", {"date_from": "2026-02-30"}),
        ("list_issued_invoices", {"date_from": "2026-03-01", "date_to": "2026-01-01"}),
    ],
)
def test_invalid_input_rejected_before_provider(name: str, args: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[name].input_model.model_validate(args)


@pytest.mark.parametrize(
    "changes",
    [
        {"secret_ref": "abraflexi/other/install-1"},
        {"credentials": {"api_url": "https://outside.invalid:5434"}},
        {"credentials": {"api_url": "https://demo.flexibee.eu:443"}},
        {"credentials": {"api_url": "https://user:pass@demo.flexibee.eu:5434"}},
        {"credentials": {"company": "../other"}},
        {"credentials": {"company": "other.company"}},
        {"credentials": {"company": "OtherCompany"}},
        {"credentials": {"pii_key": "short"}},
        {"credentials": {"username": "bad:username"}},
    ],
)
def test_bad_credentials_have_no_provider_effect(changes: dict[str, Any]) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Provider must not be contacted")

    service = AbraFlexiService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError):
        asyncio.run(service.test_connection(context(**changes)))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"winstrom": []},
        {"winstrom": {}},
        {"winstrom": {"cenik": None}},
        {"winstrom": {"cenik": [None]}},
        {"winstrom": {"cenik": [], "success": "false"}},
        {"winstrom": {"cenik": [], "success": 1}},
        {"winstrom": {"cenik": [], "@rowCount": "unknown"}},
        {"winstrom": {"cenik": [], "@rowCount": "5"}},
    ],
)
def test_malformed_provider_response_is_not_success(payload: Any) -> None:
    with pytest.raises(ConnectorError) as caught:
        invoke("list_products", {}, payload)
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR


def test_complete_pages_local_filter_and_no_dropped_cursor() -> None:
    payload = {
        "winstrom": {
            "cenik": [
                {"id": "1", "nazev": "Other"},
                {"id": "2", "nazev": "Wanted"},
            ],
            "@rowCount": "3",
        }
    }
    result, seen = invoke("list_products", {"name_contains": "Wanted", "limit": 2}, payload)
    assert result.data["items"][0]["id"] == "2"
    assert result.data["scanned"] == 2
    assert result.data["next_offset"] == 2
    assert result.data["total"] is None
    assert result.data["truncated"] is True
    assert "Wanted" not in str(seen[0].url)
    empty, _ = invoke("list_products", {"name_contains": "Absent", "limit": 2}, payload)
    assert empty.data["items"] == []
    assert empty.data["next_offset"] == 2
    next_page, seen = invoke(
        "list_products",
        {"name_contains": "Wanted", "limit": 2, "offset": 2},
        {"winstrom": {"cenik": [{"id": "3", "nazev": "Wanted"}], "@rowCount": "3"}},
    )
    assert seen[0].url.params["start"] == "2"
    assert next_page.data["next_offset"] is None
    assert next_page.data["items"][0]["id"] == "3"


def test_structured_filter_stays_in_one_company_and_relations_are_bounded() -> None:
    _, seen = invoke(
        "list_records",
        {
            "evidence": "cenik",
            "filters": [
                {"field": "nazev", "op": "like", "value": "Test item"},
                {"field": "id", "op": "gte", "value": 7},
            ],
        },
        {"winstrom": {"cenik": [], "@rowCount": "0"}},
    )
    assert seen[0].url.path == "/c/demo/cenik/(nazev like 'Test item' and id >= 7).json"
    _, seen = invoke(
        "get_record",
        {"evidence": "cenik", "record_id": "ABC", "relations": ["polozky"]},
        {"winstrom": {"cenik": [{"id": "7"}]}},
    )
    # A `code:` detail path answers with a redirect; the filter form does not.
    assert seen[0].url.path == "/c/demo/cenik/(kod='ABC').json"
    assert seen[0].url.params["relations"] == "polozky"
    assert seen[0].url.params["limit"] == "2"
    with pytest.raises(ConnectorError):
        invoke(
            "get_record",
            {"evidence": "cenik", "record_id": "ABC"},
            {"winstrom": {"cenik": [{"id": "7"}, {"id": "8"}]}},
        )
    _, seen = invoke(
        "get_record",
        {"evidence": "cenik", "record_id": "ext:SHOP:1"},
        {"winstrom": {"cenik": [{"id": "7"}]}},
    )
    assert seen[0].url.path == "/c/demo/cenik/ext:SHOP:1.json"


@pytest.mark.parametrize(
    "status,code",
    [
        (302, ErrorCode.UPSTREAM_ERROR),
        (401, ErrorCode.CREDENTIAL_INVALID),
        (404, ErrorCode.NOT_FOUND),
    ],
)
def test_provider_failures_are_safe(status: int, code: ErrorCode) -> None:
    with pytest.raises(ConnectorError) as caught:
        invoke("list_products", {}, {"error": "synthetic-password"}, status=status)
    assert caught.value.code is code
    assert "synthetic-password" not in str(caught.value)


def test_safe_connection_and_unknown_aggregate_shape() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/c/demo.json":
            return httpx.Response(200, json={"companies": {"company": [{"dbNazev": "demo"}]}})
        return httpx.Response(200, json={"winstrom": {"cenik": [], "@rowCount": "0"}})

    service = AbraFlexiService(transport=httpx.MockTransport(upstream))
    assert asyncio.run(service.test_connection(context())) == {"connected": True}
    # The safe test reads company metadata only; it must not require any module right.
    assert seen[0].url.path == "/c/demo.json" and seen[0].url.params["detail"] == "id"
    assert seen[0].headers["authorization"].startswith("Basic ")
    with pytest.raises(ConnectorError) as rejected:
        asyncio.run(
            AbraFlexiService(
                transport=httpx.MockTransport(lambda r: httpx.Response(403, json={}))
            ).test_connection(context())
        )
    assert rejected.value.code is ErrorCode.CREDENTIAL_INVALID
    assert rejected.value.provider_status == 403
    with pytest.raises(ConnectorError):
        invoke("sum_records", {"evidence": "faktura-vydana"}, {"winstrom": {"unexpected": []}})


@pytest.mark.parametrize("value", [None, True, "secret-string", "1E100", {}, 10**400])
def test_malformed_aggregate_values_are_rejected(value: Any) -> None:
    with pytest.raises(ConnectorError):
        invoke("sum_records", {"evidence": "faktura-vydana"}, {"winstrom": {"sumCelkem": value}})


def test_string_success_remains_compatible_and_malformed_properties_fail() -> None:
    result, _ = invoke(
        "list_products",
        {},
        {
            "winstrom": {
                "success": "true",
                "cenik": [],
                "@rowCount": "0",
            }
        },
    )
    assert result.data["items"] == []
    for payload in (
        {"properties": {}},
        {"properties": {"tagName": "cenik", "property": [None]}},
        {"properties": {"tagName": "other", "property": []}},
        {"properties": {"evidenceName": "cenik", "property": []}},
        {"properties": {"tagName": "cenik", "property": [{"propertyName": "../x"}]}},
    ):
        with pytest.raises(ConnectorError):
            invoke("get_evidence_properties", {"evidence": "cenik"}, payload)


def test_live_aggregate_shape_is_normalised_and_readable_in_strict_mode() -> None:
    # Grouped `$sum` shape of a live ABRA cloud instance, incl. exponent totals.
    payload = {
        "winstrom": {
            "@version": "1.0",
            "sum": {
                "sumDoklUcetni": {
                    "id": "sumDoklUcetni",
                    "sumDoklUcetni": {"type": "sumDoklUcetni", "msg": "Účetní doklady:"},
                    "values": {
                        "sumDoklCelkem": {"msg": "C:", "currency": "Kč", "value": "2.709961007E7"},
                        "sumDoklZbyvaUh": {"msg": "Z:", "currency": "Kč", "value": "1237761.58"},
                    },
                },
                "sumDoklMen": [
                    {
                        "id": "sumDoklMen",
                        "values": {
                            "sumDoklCelkem": {"msg": "C:", "currency": "PLN", "value": "659.59"}
                        },
                    },
                    {
                        "id": "sumDoklMen",
                        "values": {
                            "sumDoklCelkem": {"msg": "Celkem:", "currency": "€", "value": "-17.62"}
                        },
                    },
                ],
            },
        }
    }
    result, seen = invoke(
        "sum_records",
        {
            "evidence": "faktura-vydana",
            "filters": [{"field": "datVyst", "op": "gte", "value": "2026-01-01"}],
        },
        payload,
    )
    assert seen[0].url.path == "/c/demo/faktura-vydana/(datVyst >= '2026-01-01')/$sum.json"
    assert result.data == {
        "totals": {
            "sumDoklUcetni": {"sumDoklCelkem": "27099610.07", "sumDoklZbyvaUh": "1237761.58"}
        },
        "by_currency": [
            {"currency": "PLN", "values": {"sumDoklCelkem": "659.59"}},
            {"currency": "€", "values": {"sumDoklCelkem": "-17.62"}},
        ],
    }
    assert "msg" not in result.model_dump_json()
    for broken in (
        {"winstrom": {"sum": {}}},
        {"winstrom": {"sum": {"sumDoklUcetni": {"values": {}}}}},
        {"winstrom": {"sum": {"sumDoklUcetni": {"values": {"sumX": {"value": "1E999"}}}}}},
        {"winstrom": {"sum": {"sumDoklUcetni": {"values": {"sumX": {"value": "secret"}}}}}},
        {"winstrom": {"sum": {"sumDoklUcetni": {"values": {"evil key": {"value": "1"}}}}}},
        {"winstrom": {"sum": {"sumDoklMen": [{"values": {"sumX": {"value": "1"}}}]}}},
        {
            "winstrom": {
                "sum": {"sumDoklMen": [{"values": {"sumX": {"value": "1", "currency": "x" * 9}}}]}
            }
        },
    ):
        with pytest.raises(ConnectorError):
            invoke("sum_records", {"evidence": "faktura-vydana"}, broken)


def test_embedded_items_are_dropped_unless_requested() -> None:
    row = {
        "id": "2416",
        "kod": "FV1-000002/2025",
        "polozkyFaktury": [{"id": "1", "nazev": "Item"}],
        "skladovePolozky": [{"id": "2"}],
    }
    payload = {"winstrom": {"faktura-vydana": [row]}}
    with_items, seen = invoke("get_issued_invoice", {"invoice_id": "2416"}, payload)
    assert seen[0].url.params["relations"] == "polozky"
    assert "polozkyFaktury" in with_items.data
    without, seen = invoke(
        "get_issued_invoice", {"invoice_id": "2416", "include_items": False}, payload
    )
    assert "relations" not in seen[0].url.params
    assert "polozkyFaktury" not in without.data and "skladovePolozky" not in without.data
    assert without.data["id"] == "2416"
    movement, _ = invoke(
        "get_stock_movement",
        {"movement_id": "10414", "include_items": False},
        {"winstrom": {"skladovy-pohyb": [{"id": "10414", "skladovePolozky": [{"id": "1"}]}]}},
    )
    assert "skladovePolozky" not in movement.data
    plain, _ = invoke(
        "get_record",
        {"evidence": "faktura-vydana", "record_id": "2416", "relations": ["vazby"]},
        payload,
    )
    assert "polozkyFaktury" not in plain.data


def test_stock_status_is_not_reachable_through_generic_listing() -> None:
    with pytest.raises(ConnectorError) as caught:
        invoke("list_records", {"evidence": "stav-skladu-k-datu"}, {"winstrom": {}})
    assert caught.value.code is ErrorCode.INVALID_INPUT


def test_partner_names_are_personal_even_in_balanced_mode() -> None:
    row = {"id": "1775", "kod": "ADRIANA1", "nazev": "Adriana Nová", "stat": "code:CZ"}
    balanced = context(runtime_flags={"privacy_mode": "balanced"})
    partners, _ = invoke(
        "list_records", {"evidence": "adresar"}, {"winstrom": {"adresar": [row]}}, ctx=balanced
    )
    assert partners.data["items"][0]["stat"] == "code:CZ"
    assert "Adriana" not in partners.model_dump_json()
    assert "ADRIANA1" not in partners.model_dump_json()
    products, _ = invoke(
        "list_products", {}, {"winstrom": {"cenik": [row | {"nazev": "Aspartam"}]}}, ctx=balanced
    )
    assert products.data["items"][0]["nazev"] == "Aspartam"
