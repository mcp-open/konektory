"""Read-only Websupport (rest.websupport.sk) adapter.

Authentication per https://rest.websupport.sk/v2/docs/intro and /docs/v1.intro:
HTTP Basic with username = API key and password = hex HMAC-SHA1 of the canonical
request ``"{METHOD} {path} {unix timestamp}"`` using the API secret; the same
timestamp is sent as ``X-Date`` (v2) / ``Date`` (v1) in ISO 8601 basic UTC format.
The secret itself never leaves the runtime.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Any

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

SLUG = "websupport"
VERSION = "1.0.0"
ORIGIN = "https://rest.websupport.sk"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS = ("api_key", "api_secret")
_KEY = re.compile(r"[A-Za-z0-9._-]{8,256}")
_PATH = re.compile(r"/v[12]/[A-Za-z0-9/%._-]+")


def signature(secret: str, method: str, path: str, timestamp: int) -> str:
    canonical = f"{method} {path} {timestamp}"
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha1).hexdigest()


def signed_headers(
    api_key: str, secret: str, method: str, path: str, timestamp: int
) -> dict[str, str]:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(timestamp))
    return {
        "Authorization": basic_auth(api_key, signature(secret, method, path, timestamp)),
        "X-Date": stamp,
        "Date": stamp,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


class WebsupportService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _KEY.fullmatch(values["api_key"]) or not _KEY.fullmatch(values["api_secret"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný Websupport API klíč.")
        return values

    async def fetch(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, str, dict[str, str]]:
        if not _PATH.fullmatch(path) or ".." in path:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená Websupport cesta.")
        values = self._credentials(context)
        headers = signed_headers(
            values["api_key"], values["api_secret"], "GET", path, int(time.time())
        )
        client = UpstreamClient(
            ORIGIN, transport=self.transport, max_response_bytes=2 * 1024 * 1024
        )
        try:
            payload, source_url = await client.request_json(
                "GET", path, params=params, headers=headers
            )
        finally:
            await client.close()
        if not isinstance(payload, dict) or ("code" in payload and "message" in payload):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Websupport vrátil neplatnou odpověď.")
        return payload, source_url, values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        payload, source_url, values = await self.fetch(context, path, params)
        return private_envelope(
            payload, context, SLUG, values["api_key"], values["pii_key"], source_url
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        payload, _, _ = await self.fetch(context, "/v2/check")
        if payload.get("verified") is not True:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Websupport klíč neprošel ověřením.")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "list_services":
            return await self.get(context, "/v1/user/self/service", args)
        if name == "get_service":
            return await self.get(context, f"/v1/user/self/service/{segment(args['service_id'])}")
        if name == "list_zones":
            return await self.get(context, "/v1/user/self/zone", args)
        service = segment(args.get("service_id", 0)) if "service_id" in args else ""
        if name == "get_dns_zone":
            return await self.get(context, f"/v2/service/{service}/dns/zone")
        if name in ("list_dns_records", "list_ftp_accounts"):
            params: dict[str, Any] = {"page": args["page"], "rowsPerPage": args["rows_per_page"]}
            for key in ("name", "content"):
                if key in args:
                    params[f"filters[{key}]"] = args[key]
            resource = "dns/record" if name == "list_dns_records" else "ftp-account"
            return await self.get(context, f"/v2/service/{service}/{resource}", params)
        if name == "get_ftp_account":
            return await self.get(
                context, f"/v2/service/{service}/ftp-account/{segment(args['ftp_account_id'])}"
            )
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
