"""Read-only adapter for Luigi's Box live APIs (search, autocomplete, recommender, export).

Public endpoints (``/search``, ``/autocomplete/v2``, ``/v1/top_items``,
``/v2/trending_queries``, ``POST /v1/recommend``) identify the site by the
``tracker_id`` query parameter only. The private ``GET /v1/content_export``
requires the documented HMAC-SHA256 signature: ``Authorization: ApiAuth
{public_key}:{signature}`` over ``"{METHOD}\\n{CONTENT_TYPE}\\n{DATE}\\n{PATH}"``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from email.utils import formatdate
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

SLUG = "luigisbox"
VERSION = "1.0.0"
ORIGIN = "https://live.luigisbox.com"
USER_AGENT = "OpenMCP/1.0 (+https://openmcp.cz)"
REQUIRED_CREDENTIALS: tuple[str, ...] = ("tracker_id", "public_key", "private_key")
SIGNED_CONTENT_TYPE = "application/json; charset=utf-8"
# Exact documented read paths; nothing else is ever requested.
GET_PATHS: dict[str, str] = {
    "search": "/search",
    "autocomplete": "/autocomplete/v2",
    "top_items": "/v1/top_items",
    "trending_queries": "/v2/trending_queries",
    "content_export": "/v1/content_export",
}
# The recommender is the only POST read operation (a JSON array of request blocks).
POST_PATHS: dict[str, str] = {"recommend": "/v1/recommend"}
ALLOWED_POST_PATHS = frozenset(POST_PATHS.values())
_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")


def signature(private_key: str, method: str, content_type: str, date: str, path: str) -> str:
    """Exact canonical string from Luigi's Box "API principles" (HMAC-SHA256, Base64)."""
    data = f"{method}\n{content_type}\n{date}\n{path}"
    digest = hmac.new(private_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class ArrayBodyTransport(httpx.AsyncBaseTransport):
    """Unwrap ``{"blocks": [...]}`` into the JSON array the recommender expects.

    The shared ``UpstreamClient`` serialises only JSON objects; the Recommender
    API reads a top-level JSON array. Nothing else (URL, method, timeouts,
    bounded reading, status mapping) changes; GET requests pass through untouched.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return await self.inner.handle_async_request(request)
        wrapper = json.loads(request.content or b"{}")
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("blocks"), list):
            raise ConnectorError(ErrorCode.INTERNAL, "Neplatné tělo požadavku.")
        content = json.dumps(wrapper["blocks"], separators=(",", ":")).encode("utf-8")
        headers = request.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(content))
        return await self.inner.handle_async_request(
            httpx.Request(request.method, request.url, headers=headers, content=content)
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class LuigisboxService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # No shared client; every invocation owns and closes its own.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, SLUG, REQUIRED_CREDENTIALS)
        if not all(_KEY.fullmatch(values[key]) for key in ("tracker_id", "public_key")):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný tracker_id Luigi's Box.")
        if any(ord(char) < 32 or ord(char) == 127 for char in values["private_key"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný private_key Luigi's Box.")
        return values

    async def _request(
        self,
        context: InvocationContext,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> ToolEnvelope:
        if method == "POST" and path not in ALLOWED_POST_PATHS:
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená operace.")
        if method not in ("GET", "POST"):
            raise ConnectorError(ErrorCode.INTERNAL, "Nepovolená operace.")
        values = self._credentials(context)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if signed:
            date = formatdate(usegmt=True)
            headers["Date"] = date
            headers["Content-Type"] = SIGNED_CONTENT_TYPE
            headers["Authorization"] = "ApiAuth {}:{}".format(
                values["public_key"],
                signature(values["private_key"], method, SIGNED_CONTENT_TYPE, date, path),
            )
        query = {"tracker_id": values["tracker_id"], **(params or {})}
        client = UpstreamClient(
            ORIGIN,
            transport=ArrayBodyTransport(self.transport),
            max_response_bytes=2 * 1024 * 1024,
        )
        try:
            payload, _ = await client.request_json(
                method,
                path,
                params=query,
                json_body=json_body,
                headers=headers,
                idempotent=True,  # documented read operations; safe to retry 429/5xx
            )
        finally:
            await client.close()
        if not isinstance(payload, (dict, list)) or (
            isinstance(payload, dict) and ("error" in payload or "errors" in payload)
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Luigi's Box vrátil neplatnou odpověď.")
        return private_envelope(
            payload, context, SLUG, values["tracker_id"], values["pii_key"], ORIGIN + path
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        # Public endpoint validates the tracker_id; the signed export validates the key pair.
        await self._request(context, "GET", GET_PATHS["trending_queries"])
        await self._request(
            context, "GET", GET_PATHS["content_export"], params={"size": 1}, signed=True
        )
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        args = arguments.model_dump(exclude_none=True)
        if name == "search":
            params: dict[str, Any] = {
                "size": args["size"],
                "page": args["page"],
                "use_fixits": "true" if args["use_fixits"] else "false",
            }
            for key in ("q", "sort", "facets", "quicksearch_types", "hit_fields"):
                if key in args:
                    params[key] = args[key]
            if "filters" in args:
                params["f[]"] = args["filters"]
            if "must_filters" in args:
                params["f_must[]"] = args["must_filters"]
            return await self._request(context, "GET", GET_PATHS[name], params=params)
        if name in ("autocomplete", "top_items"):
            params = {key: args[key] for key in ("q", "type", "hit_fields") if key in args}
            return await self._request(context, "GET", GET_PATHS[name], params=params)
        if name == "trending_queries":
            return await self._request(context, "GET", GET_PATHS[name])
        if name == "recommend":
            block = {
                key: args[key]
                for key in (
                    "recommendation_type",
                    "item_ids",
                    "size",
                    "hit_fields",
                    "recommender_client_identifier",
                )
                if key in args
            }
            return await self._request(
                context, "POST", POST_PATHS[name], json_body={"blocks": [block]}
            )
        if name == "content_export":
            params = {
                key: args[key] for key in ("size", "hit_fields", "requested_types") if key in args
            }
            return await self._request(context, "GET", GET_PATHS[name], params=params, signed=True)
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
