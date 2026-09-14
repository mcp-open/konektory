from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    UpstreamClient,
)
from pydantic import SecretStr, ValidationError

from connector_gopay import schemas as s
from connector_gopay.app import build_definition
from connector_gopay.service import SLUG, TOKEN_SCOPE, GopayService, TokenFormTransport

PII_KEY = "synthetic-pii-key-0123456789abcdef01234567"
GOID = "8123456789"
CLIENT_ID = "1061399163"
CLIENT_SECRET = "synthetic-client-secret"
TOKEN = "AAAnu3YnAHRk298EsmyttFQMcbCcvmwTKK5hrJx2aGG8ZnFyBJhAvFWNmbWVSD7p"
PRIVATE = "john.doe@example.test"
INVALID = ErrorCode.CREDENTIAL_INVALID


def context(**changes: Any) -> InvocationContext:
    values = {
        "goid": GOID,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "environment": "production",
        "pii_key": PII_KEY,
    }
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


def token_response(request: httpx.Request) -> httpx.Response:
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.headers["authorization"].startswith("Basic ")
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["client_credentials"],
        "scope": [TOKEN_SCOPE],
    }
    return httpx.Response(
        200,
        json={"token_type": "bearer", "access_token": TOKEN, "expires_in": 1800},
    )


CASES: dict[str, tuple[dict[str, Any], str]] = {
    "get_payment": ({"payment_id": 3123456789}, "/api/payments/payment/3123456789"),
    "list_refunds": ({"payment_id": 3123456789}, "/api/payments/payment/3123456789/refunds"),
    "get_card": ({"card_id": 9876543210}, "/api/payments/cards/9876543210"),
    "list_payment_instruments": (
        {"currency": "CZK"},
        f"/api/eshops/eshop/{GOID}/payment-instruments/CZK",
    ),
}


@pytest.mark.anyio
async def test_all_tools_obtain_token_then_get_documented_paths() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "gate.gopay.cz"
        if request.url.path == "/api/oauth2/token":
            return token_response(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["user-agent"].startswith("OpenMCP/")
        assert not request.content
        return httpx.Response(
            200,
            json={
                "id": 3123456789,
                "state": "PAID",
                "amount": 139950,
                "currency": "CZK",
                "payer": {"contact": {"email": PRIVATE, "first_name": "John"}},
                "card_number": "440507******4448",
            },
        )

    definition = build_definition(GopayService(transport=httpx.MockTransport(upstream)))
    assert set(definition.tools) == set(CASES)
    for name, (arguments, path) in CASES.items():
        spec = definition.tools[name]
        assert spec.read_only
        result = await spec.handler(spec.input_model.model_validate(arguments), context())
        assert [request.url.path for request in seen[-2:]] == ["/api/oauth2/token", path]
        text = result.model_dump_json()
        assert PRIVATE not in text and "John" not in text and "440507" not in text
        assert TOKEN not in text and CLIENT_SECRET not in text and PII_KEY not in text
        assert result.data["id"] == 3123456789 and result.data["amount"] == 139950
        assert result.provenance.source_url == "https://gate.gopay.cz" + path
    assert len(seen) == 2 * len(CASES)


@pytest.mark.anyio
async def test_all_instruments_without_currency_and_sandbox_origin() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "gw.sandbox.gopay.com"
        if request.url.path == "/api/oauth2/token":
            return token_response(request)
        return httpx.Response(200, json={"enabledPaymentInstruments": []})

    service = GopayService(transport=httpx.MockTransport(upstream))
    sandbox = context(credentials={"environment": "sandbox"})
    spec = build_definition(service).tools["list_payment_instruments"]
    await spec.handler(spec.input_model.model_validate({}), sandbox)
    assert seen[-1].url.path == f"/api/eshops/eshop/{GOID}/payment-instruments"
    assert await service.test_connection(sandbox) == {"connected": True}
    assert seen[-1].url.path == f"/api/eshops/eshop/{GOID}/payment-instruments"
    assert seen[-1].method == "GET"


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials": {"environment": "staging"}},
        {"credentials": {"environment": "https://gate.gopay.cz"}},
        {"credentials": {"environment": ""}},
        {"credentials": {"goid": "not-a-goid"}},
        {"credentials": {"goid": ""}},
        {"credentials": {"client_id": "bad:id"}},
        {"credentials": {"client_secret": "bad\nsecret"}},
        {"credentials": {"client_secret": ""}},
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
        return token_response(request)

    service = GopayService(transport=httpx.MockTransport(upstream))
    spec = build_definition(service).tools["get_payment"]
    with pytest.raises(ConnectorError) as caught:
        await service.test_connection(context(**changes))
    assert caught.value.code in (INVALID, ErrorCode.FORBIDDEN)
    with pytest.raises(ConnectorError) as caught:
        await spec.handler(spec.input_model.model_validate({"payment_id": 1}), context(**changes))
    assert caught.value.code in (INVALID, ErrorCode.FORBIDDEN)
    assert calls == 0


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(401, json={"errors": [{"message": "private token"}]}), INVALID),
        (httpx.Response(403, text="private forbidden"), INVALID),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, json={"token_type": "bearer"}), INVALID),
        (httpx.Response(200, json={"access_token": "short"}), INVALID),
        (httpx.Response(200, json={"access_token": "bad token with spaces 0123456789"}), INVALID),
        (httpx.Response(200, json=["private"]), INVALID),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_token_failures_never_reach_data_endpoints(
    response: httpx.Response, code: ErrorCode
) -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return response

    service = GopayService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.get(context(), "/payments/payment/1")
    assert caught.value.code is code
    assert "private" not in str(caught.value)
    assert set(seen) == {"/api/oauth2/token"}


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(400, json={"errors": [{"message": "private"}]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(401, json={"errors": [{"message": "private"}]}), INVALID),
        (httpx.Response(403, text="private"), INVALID),
        (httpx.Response(404, json={"errors": [{"error_name": "PAYMENT_NOT_FOUND"}]}),
         ErrorCode.NOT_FOUND),
        (httpx.Response(429, headers={"Retry-After": "0"}), ErrorCode.RATE_LIMITED),
        (httpx.Response(500, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(503, text="private outage"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"private not json"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json={"errors": [{"message": "private"}]}), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json="private"), ErrorCode.UPSTREAM_ERROR),
        (httpx.Response(200, json=42), ErrorCode.UPSTREAM_ERROR),
    ],
)
@pytest.mark.anyio
async def test_provider_errors_map_to_safe_codes(
    response: httpx.Response, code: ErrorCode
) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth2/token":
            return token_response(request)
        return response

    service = GopayService(transport=httpx.MockTransport(upstream))
    with pytest.raises(ConnectorError) as caught:
        await service.get(context(), "/payments/payment/1")
    assert caught.value.code is code
    assert "private" not in str(caught.value) and "PAYMENT_NOT_FOUND" not in str(caught.value)


@pytest.mark.anyio
async def test_transport_refuses_any_write_request_before_egress() -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return token_response(request)

    service = GopayService(transport=httpx.MockTransport(upstream))
    client = UpstreamClient(
        "https://gate.gopay.cz/api", transport=TokenFormTransport(service.transport)
    )
    try:
        for method, path in (
            ("POST", "/payments/payment"),
            ("POST", "/payments/payment/1/refund"),
            ("POST", "/accounts/account-statement"),
            ("DELETE", "/payments/cards/1"),
            ("PUT", "/oauth2/token"),
        ):
            with pytest.raises(ConnectorError) as caught:
                await client.request_json(method, path, json_body={"amount": 1})
            assert caught.value.code is ErrorCode.INTERNAL
    finally:
        await client.close()
    assert calls == 0
    with pytest.raises(ConnectorError) as unknown:
        await service.invoke(
            "create_payment",
            build_definition().tools["get_payment"].input_model.model_validate({"payment_id": 1}),
            context(),
        )
    assert unknown.value.code is ErrorCode.NOT_FOUND
    assert calls == 0


@pytest.mark.parametrize(
    "model,arguments",
    [
        (s.PaymentID, {}),
        (s.PaymentID, {"payment_id": 0}),
        (s.PaymentID, {"payment_id": "3123456789"}),
        (s.PaymentID, {"payment_id": True}),
        (s.PaymentID, {"payment_id": 2**53}),
        (s.PaymentID, {"payment_id": 1, "url": "https://outside.invalid"}),
        (s.CardID, {"card_id": -1}),
        (s.CardID, {"card_id": 1.5}),
        (s.PaymentInstruments, {"currency": "czk"}),
        (s.PaymentInstruments, {"currency": ""}),
        (s.PaymentInstruments, {"currency": "CZK\r\n"}),
        (s.PaymentInstruments, {"goid": "1"}),
        (s.PaymentInstruments, []),
        (s.PaymentInstruments, "text"),
        (s.PaymentInstruments, None),
    ],
)
def test_arguments_are_closed_and_bounded(model: type[s.Input], arguments: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
    assert s.PaymentInstruments.model_validate({}).currency is None
    assert s.PaymentID.model_validate({"payment_id": 2**53 - 1}).payment_id == 2**53 - 1
