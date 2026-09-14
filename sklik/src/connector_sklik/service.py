"""Read-only service for Sklik API Drak (JSON over POST, explicit read-method allow-list)."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    ToolEnvelope,
    UpstreamClient,
)
from openmcp_connector_runtime.provider import credentials, private_envelope
from pydantic import BaseModel

SLUG = "sklik"
VERSION = "1.0.0"
ORIGIN = "https://api.sklik.cz"
# Pinned documented JSON endpoint of API Drak v5; the method name is the last path segment.
API_PREFIX = "/drak/json/v5"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("api_token",)
# Session handling: login by token, logout. Both are documented and change no account data.
SESSION_METHODS = frozenset({"client.loginByToken", "client.logout"})
# The complete read allow-list. `createReport` only builds a temporary server-side
# statistics view (documented read flow createReport -> readReport); nothing else is
# ever sent. Any create/update/remove/restore/set/check method is refused before egress.
READ_METHODS = frozenset(
    {
        "client.get",
        "campaigns.list",
        "groups.list",
        "keywords.list",
        "ads.list",
        "campaigns.createReport",
        "campaigns.readReport",
    }
)
ALLOWED_METHODS = SESSION_METHODS | READ_METHODS
TOOL_COLLECTIONS: dict[str, str] = {
    "list_campaigns": "campaigns",
    "list_groups": "groups",
    "list_keywords": "keywords",
    "list_ads": "ads",
}
TOOLS = frozenset({"get_client", "campaign_stats", *TOOL_COLLECTIONS})
STATS_COLUMNS = [
    "id",
    "name",
    "status",
    "type",
    "impressions",
    "clicks",
    "ctr",
    "avgCpc",
    "avgPos",
    "totalMoney",
    "conversions",
    "conversionValue",
    "transactions",
]
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,1024}")
_USER_ID = re.compile(r"[1-9][0-9]{0,17}")
_SESSION = re.compile(r"[A-Za-z0-9._~+/=-]{8,2048}")
_REPORT_ID = re.compile(r"[A-Za-z0-9_-]{1,256}")


class PositionalTransport(httpx.AsyncBaseTransport):
    """Re-encode the SDK's JSON object body as the positional JSON value Drak expects.

    The shared ``UpstreamClient`` only serialises JSON objects; API Drak JSON reads a
    bare JSON array of positional parameters (or, for ``client.loginByToken``, the bare
    token string, as in the official Postman collection). Nothing else changes.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        wrapper = json.loads(request.content or b"{}")
        if not isinstance(wrapper, dict) or "params" not in wrapper:
            raise ConnectorError(ErrorCode.INTERNAL, "Neplatné tělo požadavku.")
        content = json.dumps(wrapper["params"], ensure_ascii=True).encode("ascii")
        headers = request.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(content))
        return await self.inner.handle_async_request(
            httpx.Request(request.method, request.url, headers=headers, content=content)
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def _status_error(status: int) -> ConnectorError:
    if status in (401, 403):
        return ConnectorError(
            ErrorCode.CREDENTIAL_INVALID, "Sklik odmítl autorizaci.", provider_status=status
        )
    if status == 404:
        return ConnectorError(ErrorCode.NOT_FOUND, "Záznam nebyl nalezen.", provider_status=404)
    if status == 429:
        return ConnectorError(
            ErrorCode.RATE_LIMITED,
            "Sklik dočasně omezuje počet požadavků.",
            retryable=True,
            provider_status=429,
        )
    if status >= 500:
        return ConnectorError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "Sklik je dočasně nedostupný.",
            retryable=True,
            provider_status=status,
        )
    return ConnectorError(
        ErrorCode.UPSTREAM_ERROR, "Sklik požadavek odmítl.", provider_status=status
    )


class SklikService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not _TOKEN.fullmatch(values["api_token"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný API token Sklik.")
        user_id = values.get("user_id", "")
        if user_id and not _USER_ID.fullmatch(user_id):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné ID účtu Sklik.")
        return values

    @staticmethod
    async def _call(client: UpstreamClient, method: str, params: Any) -> dict[str, Any]:
        if method not in ALLOWED_METHODS:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená operace.")
        payload, _ = await client.request_json(
            "POST",
            f"/{method}",
            json_body={"params": params},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            idempotent=True,  # allow-listed read methods only; safe to retry 429/5xx
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), int):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Sklik vrátil neplatnou odpověď.")
        status = payload["status"]
        if status not in (200, 206):
            raise _status_error(status)
        return payload

    async def _session(
        self, context: InvocationContext
    ) -> tuple[dict[str, str], UpstreamClient, str]:
        values = self._credentials(context)
        client = UpstreamClient(
            ORIGIN + API_PREFIX,
            transport=PositionalTransport(self.transport),
            max_response_bytes=4 * 1024 * 1024,
        )
        try:
            login = await self._call(client, "client.loginByToken", values["api_token"])
        except BaseException:
            await client.close()
            raise
        session = login.get("session")
        if not isinstance(session, str) or not _SESSION.fullmatch(session):
            await client.close()
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Sklik nevrátil platnou session.")
        return values, client, session

    async def run(
        self, context: InvocationContext, name: str, args: dict[str, Any]
    ) -> ToolEnvelope:
        values, client, session = await self._session(context)
        user: dict[str, Any] = {"session": session}
        if values.get("user_id"):
            user["userId"] = int(values["user_id"])
        try:
            try:
                data = await self._read(client, user, name, args)
            finally:
                # Best effort; an unreachable logout is not an error, the session expires.
                with contextlib.suppress(ConnectorError):
                    await self._call(client, "client.logout", [{"session": user["session"]}])
        finally:
            await client.close()
        return private_envelope(
            data, context, SLUG, ORIGIN, values["pii_key"], f"{ORIGIN}{API_PREFIX}/{name}"
        )

    @classmethod
    async def _read_call(
        cls, client: UpstreamClient, user: dict[str, Any], method: str, params: list[Any]
    ) -> dict[str, Any]:
        """Call a read method and adopt the refreshed session the provider returns."""
        payload = await cls._call(client, method, [user, *params])
        refreshed = payload.get("session")
        if isinstance(refreshed, str) and _SESSION.fullmatch(refreshed):
            user["session"] = refreshed
        return payload

    async def _read(
        self, client: UpstreamClient, user: dict[str, Any], name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if name == "get_client":
            payload = await self._read_call(client, user, "client.get", [])
            return {"data": {key: payload.get(key) for key in ("user", "foreignAccounts")}}
        if name in TOOL_COLLECTIONS:
            collection = TOOL_COLLECTIONS[name]
            restriction: dict[str, Any] = {}
            if not args["include_deleted"]:
                restriction["isDeleted"] = False
            own_ids = {
                "campaigns": "campaign_ids",
                "groups": "group_ids",
                "keywords": "keyword_ids",
                "ads": "ad_ids",
            }[collection]
            if own_ids in args:
                restriction["ids"] = args[own_ids]
            if collection != "campaigns" and "campaign_ids" in args:
                restriction["campaign"] = {"ids": args["campaign_ids"]}
            if collection in ("keywords", "ads") and "group_ids" in args:
                restriction["group"] = {"ids": args["group_ids"]}
            display = {"offset": args["offset"], "limit": args["limit"]}
            payload = await self._read_call(
                client, user, f"{collection}.list", [restriction, display]
            )
            items = payload.get(collection)
            if not isinstance(items, list):
                raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Sklik vrátil neplatnou odpověď.")
            return {"data": items, "count": len(items)}
        # campaign_stats: documented two-step read (createReport -> readReport).
        restriction = {"dateFrom": args["date_from"], "dateTo": args["date_to"]}
        if not args["include_deleted"]:
            restriction["isDeleted"] = False
        if "campaign_ids" in args:
            restriction["ids"] = args["campaign_ids"]
        created = await self._read_call(
            client,
            user,
            "campaigns.createReport",
            [restriction, {"statGranularity": args["granularity"]}],
        )
        report_id = created.get("reportId")
        if not isinstance(report_id, str) or not _REPORT_ID.fullmatch(report_id):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Sklik nevrátil platný report.")
        payload = await self._read_call(
            client,
            user,
            "campaigns.readReport",
            [
                report_id,
                {
                    "offset": args["offset"],
                    "limit": args["limit"],
                    "allowEmptyStatistics": True,
                    "displayColumns": STATS_COLUMNS,
                },
            ],
        )
        report = payload.get("report")
        if not isinstance(report, list):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Sklik vrátil neplatnou odpověď.")
        total = created.get("totalCount")
        return {
            "data": report,
            "count": len(report),
            "totalCount": total if isinstance(total, int) and not isinstance(total, bool) else None,
        }

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.run(context, "get_client", {})
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        if name not in TOOLS:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
        return await self.run(context, name, arguments.model_dump(exclude_none=True))
