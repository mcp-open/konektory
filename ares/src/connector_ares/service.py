from __future__ import annotations

import re
from typing import Any

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    Provenance,
    ToolEnvelope,
    UpstreamClient,
    utc_now_iso,
)
from pydantic import ValidationError

from .schemas import LookupInput, SearchInput, SubjektData, SubjektSeznamData, SubjektSummary

ARES_ORIGIN = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
ICO_RE = re.compile(r"^\d{8}$")


def ico_checksum(ico: str) -> bool:
    if not ICO_RE.fullmatch(ico):
        return False
    digits = [int(char) for char in ico]
    total = sum(
        digit * weight for digit, weight in zip(digits[:7], range(8, 1, -1), strict=True)
    )
    remainder = total % 11
    check = 1 if remainder in (0, 10) else 0 if remainder == 1 else 11 - remainder
    return check == digits[7]


def _active_registries(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    prefix = "stavZdroje"
    return sorted(
        key[len(prefix) :].lower()
        for key, state in value.items()
        if isinstance(key, str) and key.startswith(prefix) and state == "AKTIVNI"
    )


class AresService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = UpstreamClient(
            ARES_ORIGIN,
            timeout=5,
            connect_timeout=3,
            max_attempts=2,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.close()

    async def lookup(self, arguments: LookupInput, _: InvocationContext) -> ToolEnvelope:
        if not ico_checksum(arguments.ico):
            raise ConnectorError(
                ErrorCode.INVALID_INPUT,
                "IČO musí mít 8 číslic a platný kontrolní součet.",
            )
        try:
            payload, source_url = await self.client.request_json(
                "GET", f"/ekonomicke-subjekty/{arguments.ico}"
            )
        except ConnectorError as exc:
            if exc.code == ErrorCode.NOT_FOUND:
                raise ConnectorError(
                    ErrorCode.NOT_FOUND, f"IČO {arguments.ico} nebylo v ARES nalezeno."
                ) from exc
            raise
        if not isinstance(payload, dict):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "ARES vrátil neplatný formát dat.")
        try:
            data = SubjektData.model_validate(payload)
            data.registrace = _active_registries(payload.get("seznamRegistraci"))
        except ValidationError as exc:
            raise ConnectorError(
                ErrorCode.UPSTREAM_ERROR, "ARES odpověď neodpovídá očekávanému schématu."
            ) from exc
        return ToolEnvelope(
            data=data.model_dump(mode="json"),
            provenance=Provenance(
                source_id="ares", source_url=source_url, retrieved_at=utc_now_iso()
            ),
        )

    async def search(self, arguments: SearchInput, _: InvocationContext) -> ToolEnvelope:
        body: dict[str, Any] = {
            "obchodniJmeno": arguments.obchodni_jmeno,
            "start": arguments.start,
            "pocet": arguments.pocet,
        }
        if arguments.adresa:
            body["sidlo"] = {"textovaAdresa": arguments.adresa}
        payload, source_url = await self.client.request_json(
            "POST",
            "/ekonomicke-subjekty/vyhledat",
            json_body=body,
            idempotent=True,
        )
        if not isinstance(payload, dict):
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "ARES vrátil neplatný formát dat.")
        try:
            total = int(payload.get("pocetCelkem", 0))
            raw_items = payload.get("ekonomickeSubjekty") or []
            if not isinstance(raw_items, list):
                raise TypeError
            items = [SubjektSummary.model_validate(item) for item in raw_items]
            data = SubjektSeznamData(
                pocet_celkem=total,
                start=arguments.start,
                pocet=arguments.pocet,
                subjekty=items,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ConnectorError(
                ErrorCode.UPSTREAM_ERROR, "ARES odpověď neodpovídá očekávanému schématu."
            ) from exc
        warnings: list[str] = []
        if total == 0:
            warnings.append("Žádný subjekt neodpovídá zadanému filtru.")
        elif total > len(items):
            warnings.append(
                f"Nalezeno {total} subjektů, vráceno {len(items)}; použijte přesnější filtr "
                "nebo parametr start."
            )
        return ToolEnvelope(
            data=data.model_dump(mode="json"),
            provenance=Provenance(
                source_id="ares", source_url=source_url, retrieved_at=utc_now_iso()
            ),
            warnings=warnings,
        )

