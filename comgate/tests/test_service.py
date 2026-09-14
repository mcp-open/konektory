from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext
from pydantic import SecretStr, ValidationError

from connector_comgate import schemas as s
from connector_comgate.app import build_definition
from connector_comgate.service import SLUG, ComgateService

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
MERCHANT = "123456"
SECRET = "synthetic-merchant-secret"
PRIVATE = "payer@example.test"
INVALID = ErrorCode.CREDENTIAL_INVALID
UPSTREAM = ErrorCode.UPSTREAM_ERROR


def context(**changes: Any) -> InvocationContext:
    values = {"merchant": MERCHANT, "secret": SECRET, "pii_key": PII_KEY}
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


CASES: dict[str, tuple[dict[str, Any], str]] = {
    "get_payment": ({"trans_id": "AB12-CD34-EF56"}, "/v2.0/payment/transId/AB12-CD34-EF56.json"),
    "list_transfers": (
        {"date": "2025-04-25", "test": True},
        "/v2.0/transferList/date/2025-04-25.json",
    ),
    "get_transfer": ({"transfer_id": 1234567}, "/v2.0/singleTransfer/transferId/1234567.json"),
    "list_methods": ({"lang": "cs", "currency": "CZK", "country": "CZ"}, "/v2.0/method.json"),
}


@pytest.mark.anyio
async def test_all_tools_use_documented_get_paths_with_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.host == "payments.comgate.cz"
        expected = base64.b64encode(f"{MERCHANT}:{SECRET}".encode()).decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        assert request.headers["user-agent"].startswith("OpenMCP/")
        assert not request.content
        if request.url.path.startswith("/v2.0/transferList"):
            return httpx.Response(
                200,
                json=[{"transferId": 1234567, "transferDate": "2023-01-25", "variableSymbol": "1"}],
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "OK",
                "price": "10000",
                "curr": "CZK",
                "email": PRIVATE,
                "payerName": "John Doe",
                "status": "PAID",
                "transId": "AB12-CD34-EF56",
            },
        )

    definition = build_definition(ComgateService(transport=httpx.MockTransport(upstream)))
    assert set(definition.tools) == set(CASES)
    for name, (arguments, path) in CASES.items():
        spec = definition.tools[name]
        assert spec.read_only
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert seen[-1].url.path == path
        text = result.model_dump_json()
        assert PRIVATE not in text and "John Doe" not in text
        assert SECRET not in text and PII_KEY not in text
        assert result.provenance.source_url == "https://payments.comgate.cz" + path
    assert len(seen) == len(CASES)
    assert seen[1].url.params["test"] == "true"
    assert "test" not in seen[2].url.params
    assert dict(seen[3].url.params) == {"lang": "cs", "curr": "CZK", "country": "CZ"}
    assert "test" not in seen[0].url.params and not seen[0].url.query


@pytest.mark.anyio
async def test_test_connection_and_optional_parameters_are_omitted() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"methods": [{"id": "CARD_CZ_CSOB_2", "name": "x"}]})

    service = ComgateService(transport=httpx.MockTransport(upstream))
    assert await service.test_connection(context()) == {"connected": True}
    assert seen[-1].url.path == "/v2.0/method.json" and not seen[-1].url.query
    spec = build_definition(service).tools["list_methods"]
    await spec.handler(spec.input_model.model_validate({}), context())
    assert not seen[-1].url.query
    spec = build_definition(service).tools["get_transfer"]
    await spec.handler(spec.input_model.model_validate({"transfer_id": 7, "test": True}), context())
    assert seen[-1].url.path == "/v2.0/singleTransfer/transferId/7.json"
    assert seen[-1].url.params["test"] == "true"


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"merchant": "bad merchant"}},
        {"credentials": {"merchant": "bad:merchant"}},
        {"credentials": {"merchant": ""}},
        {"credentials": {"secret": "bad\nsecret"}},
        {"credentials": {"secret": ""}},
        {"credentials": {"pii_key": "short"}},
        {"secret_ref": f"{SLUG}/other/install-1"},
        {"secret_ref": "other/workspace-1/install-1"},
        {"secret_version": None},
        {"provider_credential": None},
        {"provider_credential": {}},
    ],
)
@pytest.mark.anyio
async def test_bad_credentials_fail_closed_before_any_request(changes: dict[str, Any]) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"methods": []})

    service = ComgateService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["get_payment"]
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (INVALID, ErrorCode.FORBIDDEN)
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(
            spec.input_model.model_validate({"trans_id": "AB12-CD34-EF56"}), context(**changes)
        )
    assert caught.value.code in (INVALID, ErrorCode.FORBIDDEN)
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, json={"code": 1400, "message": "private"}), UPSTREAM),
        (httpx.Response(401, text="private"), INVALID),
        (httpx.Response(403, json={"code": 1400, "message": "private"}), INVALID),
        (httpx.Response(404, text="private"), ErrorCode.NOT_FOUND),
        (httpx.Response(405, text="private"), UPSTREAM),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(204), UPSTREAM),
        (httpx.Response(200, content=b"private not json"), UPSTREAM),
        (httpx.Response(200, content=b"<xml>private</xml>"), UPSTREAM),
        (httpx.Response(200, json={"code": 1100, "message": "private unknown"}), UPSTREAM),
        (httpx.Response(200, json={"code": 1200, "message": "private db"}), UPSTREAM),
        (httpx.Response(200, json={"code": "0", "message": "private"}), UPSTREAM),
        (httpx.Response(200, json="private"), UPSTREAM),
        (httpx.Response(200, json=42), UPSTREAM),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_map_to_safe_codes(
    response: httpx.Response, code: ErrorCode
) -> None:
    service = ComgateService(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(ConnectorError) as caught:
        await service.get(context(), "/payment/transId/AB12-CD34-EF56.json")
    assert caught.value.code is code
    assert "private" not in str(caught.value)


@pytest.mark.anyio
async def test_unknown_tool_is_refused_without_request() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"code": 0})

    service = ComgateService(transport=httpx.MockTransport(upstream))
    arguments = build_definition().tools["get_payment"].input_model.model_validate(
        {"trans_id": "AB12-CD34-EF56"}
    )
    for name in ("create_payment", "cancel_payment", "refund_payment", "capture_preauth"):
        with pytest.raises(ConnectorError) as caught:
            await service.invoke(name, arguments, context())
        assert caught.value.code is ErrorCode.NOT_FOUND
    assert calls == 0


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.TransID, {}),
        (s.TransID, {"trans_id": ""}),
        (s.TransID, {"trans_id": "AB12CD34EF56"}),
        (s.TransID, {"trans_id": "AB12-CD34-EF56/refund"}),
        (s.TransID, {"trans_id": "AB12-CD34-EF5\n"}),
        (s.TransID, {"trans_id": "../../v1.0/status"}),
        (s.TransID, {"trans_id": 1234}),
        (s.TransID, {"trans_id": "AB12-CD34-EF56", "url": "https://outside.invalid"}),
        (s.TransferList, {}),
        (s.TransferList, {"date": "25.04.2025"}),
        (s.TransferList, {"date": "2025-13-01"}),
        (s.TransferList, {"date": "2025-02-30"}),
        (s.TransferList, {"date": "2025-04-25", "test": "true"}),
        (s.TransferID, {"transfer_id": 0}),
        (s.TransferID, {"transfer_id": "1234567"}),
        (s.TransferID, {"transfer_id": True}),
        (s.TransferID, {"transfer_id": 2**53}),
        (s.MethodList, {"currency": "czk"}),
        (s.MethodList, {"country": "XX"}),
        (s.MethodList, {"lang": "klingon"}),
        (s.MethodList, {"price": 100}),
        (s.MethodList, []),
        (s.MethodList, "text"),
        (s.MethodList, None),
    ],
)
def test_arguments_are_closed_and_bounded(model: type[s.Input], arguments: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
    assert s.MethodList.model_validate({}).currency is None
    assert s.TransferList.model_validate({"date": "2024-02-29"}).test is False
    assert s.TransID.model_validate({"trans_id": "ab12-cd34-ef56"}).trans_id == "ab12-cd34-ef56"
