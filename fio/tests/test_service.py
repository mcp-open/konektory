from __future__ import annotations

from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_fio.app import build_definition
from connector_fio.service import POINTER_WARNING, SLUG, FioService, movement

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
TOKEN = "s" * 16 + "YnTheTic0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
assert len(TOKEN) == 64
STATEMENT: dict[str, Any] = {
    "accountStatement": {
        "info": {
            "accountId": "2400222222",
            "bankId": "2010",
            "currency": "CZK",
            "iban": "CZ7920100000002400222222",
            "bic": "FIOBCZPPXXX",
            "openingBalance": 185.03,
            "closingBalance": 185.05,
            "dateStart": "2023-08-25+0200",
            "dateEnd": "2023-08-31+0200",
            "yearList": None,
            "idList": None,
            "idFrom": 1155172472,
            "idTo": 1155172472,
            "idLastDownload": None,
        },
        "transactionList": {
            "transaction": [
                {
                    "column22": {"value": 1155172472, "name": "ID pohybu", "id": 22},
                    "column0": {"value": "2023-08-28+0200", "name": "Datum", "id": 0},
                    "column1": {"value": -352.0, "name": "Objem", "id": 1},
                    "column14": {"value": "CZK", "name": "Měna", "id": 14},
                    "column10": {
                        "value": "Private Counterparty",
                        "name": "Název protiúčtu",
                        "id": 10,
                    },
                    "column9": {"value": "Private Person", "name": "Provedl", "id": 9},
                    "column17": {"value": 2107642322, "name": "ID pokynu", "id": 17},
                    "column99": {"value": "x", "name": "Neznámý sloupec", "id": 99},
                    "column5": None,
                }
            ]
        },
    }
}


def context(**changes: Any) -> InvocationContext:
    values = {"token": TOKEN, "pii_key": PII_KEY}
    values.update(changes.pop("credentials", {}))
    return InvocationContext(
        request_id="req-1",
        subject="subject-1",
        workspace_id="workspace-1",
        installation_id="install-1",
        secret_ref=f"{SLUG}/workspace-1/install-1",
        secret_version=1,
        provider_credential={key: SecretStr(value) for key, value in values.items()},
    ).model_copy(update=changes)


def upstream_ok(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/statement"):
        return httpx.Response(200, text="2023,7", headers={"Content-Type": "text/plain"})
    return httpx.Response(200, json=STATEMENT)


CASES: dict[str, tuple[dict[str, Any], str, str]] = {
    "list_transactions": (
        {"date_from": "2023-08-25", "date_to": "2023-08-31"},
        f"/v1/rest/periods/{TOKEN}/2023-08-25/2023-08-31/transactions.json",
        "/v1/rest/periods/{token}/2023-08-25/2023-08-31/transactions.json",
    ),
    "last_transactions": (
        {},
        f"/v1/rest/last/{TOKEN}/transactions.json",
        "/v1/rest/last/{token}/transactions.json",
    ),
    "get_statement": (
        {"year": 2023, "statement_id": 7},
        f"/v1/rest/by-id/{TOKEN}/2023/7/transactions.json",
        "/v1/rest/by-id/{token}/2023/7/transactions.json",
    ),
    "last_statement_number": (
        {},
        f"/v1/rest/lastStatement/{TOKEN}/statement",
        "/v1/rest/lastStatement/{token}/statement",
    ),
}


@pytest.mark.anyio
async def test_all_tools_get_documented_paths_and_never_expose_the_token() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return upstream_ok(request)

    definition = build_definition(FioService(transport=httpx.MockTransport(upstream)))
    assert set(definition.tools) == set(CASES)
    for name, (arguments, path, placeholder) in CASES.items():
        spec = definition.tools[name]
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        request = seen[-1]
        assert spec.read_only and request.method == "GET"
        assert request.url.scheme == "https" and request.url.host == "fioapi.fio.cz"
        assert request.url.path == path and not request.url.query
        assert "authorization" not in request.headers
        assert request.headers["user-agent"].startswith("OpenMCP/")
        text = result.model_dump_json()
        assert TOKEN not in text and PII_KEY not in text and "Private" not in text
        assert result.provenance.source_url == "https://fioapi.fio.cz" + placeholder
        assert "{token}" in result.provenance.source_url
        if name == "last_statement_number":
            assert result.data["id"] == 7 and len(result.data) == 2
            assert "Poslední oficiální výpis: rok 2023, číslo 7." in result.warnings
        else:
            assert result.data["count"] == 1
            assert result.data["items"][0]["id"] == 1155172472
            assert result.data["items"][0]["amount"] == -352.0
            assert "currency" in result.data["items"][0]  # value pseudonymised
            assert "counterAccountName" not in result.data["items"][0]  # pseudonymised key
    assert all(request.url.path.startswith("/v1/rest/") for request in seen)
    assert not any(
        "set-last" in request.url.path or "import" in request.url.path for request in seen
    )


@pytest.mark.anyio
async def test_last_transactions_warns_about_pointer_only_when_data_arrived() -> None:
    empty = {
        "accountStatement": {"info": STATEMENT["accountStatement"]["info"], "transactionList": None}
    }
    reply: dict[str, Any] = {"body": STATEMENT}
    service = FioService(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply["body"]))
    )
    spec = build_definition(service).tools["last_transactions"]
    result = await spec.handler(spec.input_model.model_validate({}), context())
    assert POINTER_WARNING in result.warnings
    reply["body"] = empty
    result = await spec.handler(spec.input_model.model_validate({}), context())
    assert result.data["count"] == 0 and POINTER_WARNING not in result.warnings


@pytest.mark.anyio
async def test_safe_test_uses_statement_number_and_parses_text() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return upstream_ok(request)

    service = FioService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[0].url.path == f"/v1/rest/lastStatement/{TOKEN}/statement"
    for body in (b"private", b"2023;7", b"20237", b"2023,7,1", b"1" * 100, b"{}"):
        broken = FioService(
            transport=httpx.MockTransport(lambda r, b=body: httpx.Response(200, content=b))
        )
        with pytest.raises(ConnectorError) as caught:
            await broken.test_connection(context())
        assert caught.value.code is ErrorCode.UPSTREAM_ERROR
        assert "private" not in str(caught.value) and TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"token": "short"}},
        {"credentials": {"token": "x" * 63}},
        {"credentials": {"token": "x" * 65}},
        {"credentials": {"token": "x" * 63 + "/"}},
        {"credentials": {"token": "x" * 63 + "."}},
        {"credentials": {"token": "x" * 40 + "\r\nHost: evil" + "x" * 12}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_version": None},
        {"provider_credential": None},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return upstream_ok(request)

    service = FioService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (ErrorCode.CREDENTIAL_INVALID, ErrorCode.FORBIDDEN)
    spec = build_definition(service).tools["last_transactions"]
    with pytest.raises(ConnectorError):
        await spec.handler(spec.input_model.model_validate({}), context(**changes))
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(404, text="private wrong url"), ErrorCode.NOT_FOUND),
        (httpx.Response(409, text="private conflict"), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private token error"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(401, text="private"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(403, text="private"), ErrorCode.CREDENTIAL_INVALID),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(503, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (
            httpx.Response(302, headers={"Location": "https://outside.invalid/"}),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=[]), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"accountStatement": "private"}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"accountStatement": {"info": None}}), ErrorCode.UPSTREAM_ERROR),
        (
            httpx.Response(
                200,
                json={"accountStatement": {"info": {}, "transactionList": {"transaction": "x"}}},
            ),
            ErrorCode.UPSTREAM_ERROR,
        ),
        (
            httpx.Response(
                200,
                json={
                    "accountStatement": {
                        "info": {},
                        "transactionList": {"transaction": [{"evil": 1}]},
                    }
                },
            ),
            ErrorCode.UPSTREAM_ERROR,
        ),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_never_leak_text_or_token(
    response: httpx.Response, code: ErrorCode
) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    service = FioService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["list_transactions"]
    arguments = spec.input_model.model_validate(
        {"date_from": "2023-08-25", "date_to": "2023-08-31"}
    )
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(arguments, context())
    assert caught.value.code is code
    assert "private" not in str(caught.value) and TOKEN not in str(caught.value)
    assert calls == 1  # never retried: Fio allows one request per token per 30 s


@pytest.mark.anyio
async def test_network_failure_is_safe_and_not_retried() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(f"private {request.url}", request=request)

    service = FioService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["last_transactions"]
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({}), context())
    assert caught.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    assert TOKEN not in str(caught.value) and "private" not in str(caught.value)
    assert calls == 1


def test_movement_flattening_uses_documented_names() -> None:
    row = STATEMENT["accountStatement"]["transactionList"]["transaction"][0]
    flat = movement(row)
    assert flat["id"] == 1155172472 and flat["instructionId"] == 2107642322
    assert flat["executor"] == "Private Person" and flat["column99"] == "x"
    assert "column5" not in flat
    for bad in (
        "x",
        {"column1": "x"},
        {"other": {"value": 1}},
        {"column1": {"value": 1}, "x": None},
    ):
        with pytest.raises(ConnectorError):
            movement(bad)


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_transactions", {}),
        ("list_transactions", {"date_from": "2023-08-25"}),
        ("list_transactions", {"date_from": "2023-08-25", "date_to": "2023-08-24"}),
        ("list_transactions", {"date_from": "2023-01-01", "date_to": "2023-04-02"}),
        ("list_transactions", {"date_from": "2023-13-01", "date_to": "2023-12-31"}),
        ("list_transactions", {"date_from": "2023-02-30", "date_to": "2023-03-01"}),
        ("list_transactions", {"date_from": "25.08.2023", "date_to": "2023-08-31"}),
        (
            "list_transactions",
            {"date_from": "2023-08-25", "date_to": "2023-08-31", "format": "xml"},
        ),
        ("list_transactions", {"date_from": "2023-08-25", "date_to": "2023-08-31", "token": "x"}),
        ("get_statement", {}),
        ("get_statement", {"year": 1999, "statement_id": 1}),
        ("get_statement", {"year": 2101, "statement_id": 1}),
        ("get_statement", {"year": 2023, "statement_id": 0}),
        ("get_statement", {"year": "2023", "statement_id": 1}),
        ("get_statement", {"year": 2023, "statement_id": True}),
        ("get_statement", {"year": 2023, "statement_id": 1, "url": "https://outside.invalid"}),
        ("last_transactions", {"since": "2023-01-01"}),
        ("last_transactions", []),
        ("last_statement_number", "text"),
        ("last_statement_number", None),
    ],
)
def test_arguments_are_closed_and_bounded(tool: str, arguments: Any) -> None:
    with pytest.raises(ValidationError):
        build_definition().tools[tool].input_model.model_validate(arguments)
