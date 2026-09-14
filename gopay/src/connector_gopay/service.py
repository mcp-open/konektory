"""Read-only adapter for the GoPay REST API (doc.gopay.com).

Every invocation first exchanges ``client_id:client_secret`` for a short-lived
Bearer token (``POST /api/oauth2/token``, form-encoded ``client_credentials``
grant) and then performs exactly one documented GET. The token POST is the only
non-GET request the adapter can ever send; the transport wrapper refuses any
other method or path before it reaches the network.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    ToolEnvelope,
    UpstreamClient,
)
from openmcp_connector_runtime.provider import basic_auth, credentials, private_envelope, segment
from pydantic import BaseModel

SLUG = "gopay"
VERSION = "1.0.0"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
# Only the two documented gateway addresses exist; the credential ``environment``
# selects one, a free URL is never accepted.
ORIGINS: dict[str, str] = {
    "production": "https://gate.gopay.cz",
    "sandbox": "https://gw.sandbox.gopay.com",
}
API = "/api"
TOKEN_PATH = "/oauth2/token"
# ``payment-create`` tokens may only create payments; inquiries need ``payment-all``.
# The adapter still never sends anything but the token POST and documented GETs.
TOKEN_SCOPE = "payment-all"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("goid", "client_id", "client_secret", "environment")
_GOID = re.compile(r"[0-9]{1,20}")
_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_TOKEN = re.compile(r"[\x21-\x7e]{16,4096}")


class TokenFormTransport(httpx.AsyncBaseTransport):
    """Re-encode the token request as a form and refuse every other non-GET request.

    The shared ``UpstreamClient`` serialises bodies as JSON; the GoPay token endpoint
    only reads ``application/x-www-form-urlencoded``. Nothing else (URL, timeouts,
    bounded reading, status mapping) changes.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await self.inner.handle_async_request(request)
        if request.method != "POST" or request.url.path != API + TOKEN_PATH:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolený požadavek na GoPay.")
        fields = json.loads(request.content or b"{}")
        if not isinstance(fields, dict):
            raise ConnectorError(ErrorCode.INTERNAL, "Neplatné tělo požadavku.")
        content = urlencode({str(k): str(v) for k, v in fields.items()}).encode("ascii")
        headers = request.headers.copy()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(content))
        return await self.inner.handle_async_request(
            httpx.Request(request.method, request.url, headers=headers, content=content)
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class GopayService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        origin = ORIGINS.get(values["environment"])
        if origin is None:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neznámé prostředí GoPay.")
        if not _GOID.fullmatch(values["goid"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné GoID.")
        if not _CLIENT_ID.fullmatch(values["client_id"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné Client ID GoPay.")
        basic_auth(values["client_id"], values["client_secret"])
        return values, origin

    @staticmethod
    async def _token(client: UpstreamClient, values: dict[str, str]) -> str:
        payload, _ = await client.request_json(
            "POST",
            TOKEN_PATH,
            json_body={"grant_type": "client_credentials", "scope": TOKEN_SCOPE},
            headers={
                "Authorization": basic_auth(values["client_id"], values["client_secret"]),
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            idempotent=True,  # documented token exchange; safe to retry 429/5xx
        )
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "GoPay nevydalo token.")
        return token

    async def get(self, context: InvocationContext, path: str) -> ToolEnvelope:
        values, origin = self._credentials(context)
        client = UpstreamClient(
            origin + API,
            transport=TokenFormTransport(self.transport),
            max_response_bytes=2 * 1024 * 1024,
        )
        try:
            token = await self._token(client, values)
            payload, _ = await client.request_json(
                "GET",
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and "errors" in payload
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "GoPay vrátilo neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, values["goid"], values["pii_key"], origin + API + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        values, _ = self._credentials(context)
        await self.get(context, f"/eshops/eshop/{segment(values['goid'])}/payment-instruments")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "get_payment":
            return await self.get(context, f"/payments/payment/{segment(args['payment_id'])}")
        if name == "list_refunds":
            payment = segment(args["payment_id"])
            return await self.get(context, f"/payments/payment/{payment}/refunds")
        if name == "get_card":
            return await self.get(context, f"/payments/cards/{segment(args['card_id'])}")
        if name == "list_payment_instruments":
            values, _ = self._credentials(context)
            path = f"/eshops/eshop/{segment(values['goid'])}/payment-instruments"
            if "currency" in args:
                path += f"/{segment(args['currency'])}"
            return await self.get(context, path)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
