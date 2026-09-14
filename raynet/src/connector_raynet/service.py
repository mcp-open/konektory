from __future__ import annotations

import re
from typing import Any, get_args

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    Provenance,
    ToolEnvelope,
    UpstreamClient,
)
from openmcp_connector_runtime.models import utc_now_iso
from openmcp_connector_runtime.provider import basic_auth, credentials, private_envelope, segment
from pydantic import BaseModel

from .schemas import Codebook, Resource

ORIGIN = "https://app.raynet.cz/api/v2"
SEARCH = {
    "search_companies": "company",
    "search_persons": "person",
    "search_business_cases": "businessCase",
    "search_leads": "lead",
}
DETAIL = {
    "get_company": ("company", "company_id"),
    "get_person": ("person", "person_id"),
    "get_business_case": ("businessCase", "business_case_id"),
    "get_lead": ("lead", "lead_id"),
}


class RaynetService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # Every invocation owns and closes its own credential-bearing client.

    @staticmethod
    def _credentials(context: InvocationContext) -> dict[str, str]:
        values = credentials(context, "raynet", ("instance_name", "username", "api_key"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", values["instance_name"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatná instance RAYNET.")
        basic_auth(values["username"], values["api_key"])
        return values

    async def get(
        self, context: InvocationContext, path: str, params: dict[str, Any] | None = None
    ) -> ToolEnvelope:
        values = self._credentials(context)
        client = UpstreamClient(ORIGIN, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            payload, _ = await client.request_json(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": basic_auth(values["username"], values["api_key"]),
                    "X-Instance-Name": values["instance_name"],
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or not isinstance(payload.get("data"), (dict, list))
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "RAYNET vrátil neplatnou odpověď.")
        rows = payload["data"]
        if isinstance(rows, list) and not all(isinstance(row, dict) for row in rows):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "RAYNET vrátil neplatné záznamy.")
        if params and "limit" in params and (
            not isinstance(rows, list) or len(rows) > params["limit"]
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "RAYNET překročil požadovanou stránku.")
        if "totalCount" in payload and (
            type(payload["totalCount"]) is not int or not 0 <= payload["totalCount"] <= 2**53 - 1
        ):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "RAYNET vrátil neplatné stránkování.")
        if isinstance(rows, list) and params and "totalCount" in payload:
            offset = params.get("offset", 0)
            total = payload["totalCount"]
            if (rows and offset + len(rows) > total) or (not rows and offset < total):
                raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "RAYNET vrátil nesouvislou stránku.")
        return private_envelope(
            payload, context, "raynet", values["instance_name"], values["pii_key"], ORIGIN
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        await self.get(context, "/security/info")
        return {"connected": True}

    async def invoke(
        self, name: str, arguments: BaseModel, context: InvocationContext
    ) -> ToolEnvelope:
        self._credentials(context)
        args = arguments.model_dump(exclude_none=True)
        if name == "raynet_resources":
            return ToolEnvelope(
                data={"resources": list(get_args(Resource)), "codebooks": list(get_args(Codebook))},
                provenance=Provenance(
                    source_id="raynet",
                    source_url=ORIGIN,
                    retrieved_at=utc_now_iso(),
                    freshness="cached",
                ),
                warnings=["Pevný katalog adaptéru; dostupnost závisí na právech provider účtu."],
            )
        if name == "raynet_whoami":
            return await self.get(context, "/security/info")
        if name in SEARCH:
            if "company_id" in args:
                args["primaryRelationship-company-id"] = args.pop("company_id")
            return await self.get(context, f"/{SEARCH[name]}/", args)
        if name in DETAIL:
            resource, field = DETAIL[name]
            return await self.get(context, f"/{resource}/{segment(args[field])}/")
        if name in {"get_company_by_ext", "get_person_by_ext"}:
            resource = "company" if name == "get_company_by_ext" else "person"
            return await self.get(context, f"/{resource}/ext/{segment(args['ext_id'])}/")
        if name == "raynet_list":
            params = args.get("filters", {}) | {"limit": args["limit"], "offset": args["offset"]}
            return await self.get(context, f"/{args['resource']}/", params)
        if name == "raynet_get":
            return await self.get(context, f"/{args['resource']}/{segment(args['record_id'])}/")
        if name == "raynet_get_by_ext":
            return await self.get(context, f"/{args['resource']}/ext/{segment(args['ext_id'])}/")
        if name == "raynet_codebook":
            return await self.get(
                context, f"/{args['name']}/", {"limit": args["limit"], "offset": args["offset"]}
            )
        raise ConnectorError(ErrorCode.NOT_FOUND, "Nástroj nebyl nalezen.")
