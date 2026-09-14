from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from openmcp_connector_runtime import (
    ConnectorError,
    ErrorCode,
    InvocationContext,
    Provenance,
    SecretResolver,
    ToolEnvelope,
    UpstreamClient,
    utc_now_iso,
)
from openmcp_connector_runtime.provider import privacy_mode

from .oauth import DotykackaOAuth
from .pii import Pseudonymizer, derive_tenant_key
from .schemas import EmptyInput, ListOrdersInput, SalesSummaryInput

BASE_URL = "https://api.dotykacka.cz/v2"
MAX_RECORDS = 500
PAGE_LIMIT = 100
REQUIRED_SECRETS = ("refresh_token", "cloud_id", "pii_key")


class DotykackaService:
    def __init__(
        self,
        resolver: SecretResolver | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timezone: str | None = None,
    ) -> None:
        self.resolver = resolver
        self.timezone_name: str = (
            timezone or os.environ.get("DOTYKACKA_TIMEZONE") or "Europe/Prague"
        )
        self.client = UpstreamClient(
            BASE_URL,
            timeout=10,
            connect_timeout=3,
            max_attempts=2,
            transport=transport,
        )
        self.oauth = DotykackaOAuth(self.client)

    async def close(self) -> None:
        await self.client.close()
        resolver_close = (
            getattr(self.resolver, "close", None) if self.resolver is not None else None
        )
        if resolver_close is not None:
            await resolver_close()

    async def _resolved_secrets(self, context: InvocationContext) -> dict[str, str]:
        if context.secret_ref is None or context.secret_version is None:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Chybí odkaz na credentials.")
        expected_reference = f"dotykacka/{context.workspace_id}/{context.installation_id}"
        if context.secret_ref != expected_reference:
            raise ConnectorError(ErrorCode.FORBIDDEN, "Credentials nejsou svázané s instalací.")
        if context.provider_credential is not None:
            values = {
                key: value.get_secret_value()
                for key, value in context.provider_credential.items()
            }
        elif self.resolver is not None:
            # Explicit compatibility seam for isolated tests. Production
            # Compose supplies one body-bound credential from core and mounts
            # no OpenBao identity into the connector.
            values = dict(await self.resolver.resolve(context.secret_ref, context.secret_version))
        else:
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Credentials nejsou dostupné.")
        if any(not values.get(key) for key in REQUIRED_SECRETS):
            raise ConnectorError(
                ErrorCode.CREDENTIAL_INVALID, "Credentials Dotykačky nejsou úplné."
            )
        return values

    def _timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConnectorError(ErrorCode.INTERNAL, "Časové pásmo konektoru je neplatné.") from exc

    def _day(self, value: str, label: str) -> datetime:
        try:
            if len(value) != 10:
                raise ValueError
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ConnectorError(
                ErrorCode.INVALID_INPUT, f"{label} musí být datum ve formátu YYYY-MM-DD."
            ) from exc
        local_midnight = datetime.combine(
            parsed, datetime.min.time(), tzinfo=self._timezone()
        )
        return local_midnight.astimezone(UTC)

    def _date_filter(self, date_from: str | None, date_to: str | None) -> str | None:
        clauses: list[str] = []
        if date_from:
            start = self._day(date_from, "date_from").strftime("%Y-%m-%dT%H:%M:%S.000Z")
            clauses.append(f"created|gteq|{start}")
        if date_to:
            end = self._day(date_to, "date_to").strftime("%Y-%m-%dT%H:%M:%S.000Z")
            clauses.append(f"created|lt|{end}")
        return ";".join(clauses) or None

    async def _session(self, context: InvocationContext) -> tuple[dict[str, str], Pseudonymizer]:
        secrets = await self._resolved_secrets(context)
        pseudo = Pseudonymizer(
            derive_tenant_key(secrets["pii_key"], context, secrets["cloud_id"]),
            mode=privacy_mode(context),
            secret_values=frozenset(secrets.values()),
        )
        return secrets, pseudo

    async def _get(
        self,
        context: InvocationContext,
        resource: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, str, Pseudonymizer, str]:
        secrets, pseudo = await self._session(context)
        cloud_id = secrets["cloud_id"]
        assert context.secret_version is not None
        token, cache_key = await self.oauth.access_token(
            secrets["refresh_token"], cloud_id, context.secret_version
        )
        cloud = quote(cloud_id, safe="")
        path = f"/clouds/{cloud}/{resource}" if resource else f"/clouds/{cloud}"
        try:
            payload, source_url = await self.client.request_json(
                "GET", path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except ConnectorError as exc:
            if exc.code != ErrorCode.CREDENTIAL_INVALID:
                raise
            self.oauth.invalidate(cache_key)
            token, _ = await self.oauth.access_token(
                secrets["refresh_token"], cloud_id, context.secret_version
            )
            payload, source_url = await self.client.request_json(
                "GET", path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        return payload, source_url, pseudo, cloud_id

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        raw = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    async def get_cloud_info(
        self, _arguments: EmptyInput, context: InvocationContext
    ) -> ToolEnvelope:
        payload, source_url, pseudo, _cloud_id = await self._get(context, "")
        return ToolEnvelope(
            data=pseudo.sanitize(payload, profile="cloud"),
            provenance=Provenance(
                source_id="dotykacka", source_url=source_url, retrieved_at=utc_now_iso()
            ),
        )

    async def list_orders(
        self, arguments: ListOrdersInput, context: InvocationContext
    ) -> ToolEnvelope:
        params: dict[str, Any] = {
            "sort": "created",
            "limit": arguments.limit,
            "page": arguments.page,
        }
        date_filter = self._date_filter(arguments.date_from, arguments.date_to)
        if date_filter:
            params["filter"] = date_filter
        if arguments.include_items:
            params["include"] = "orderItems,moneyLogs"
        payload, source_url, pseudo, _ = await self._get(context, "orders", params=params)
        return ToolEnvelope(
            data=pseudo.sanitize(payload, profile="orders"),
            provenance=Provenance(
                source_id="dotykacka", source_url=source_url, retrieved_at=utc_now_iso()
            ),
        )

    async def _collect_orders(
        self,
        context: InvocationContext,
        date_from: str | None,
        date_to: str | None,
    ) -> tuple[list[dict[str, Any]], bool, str]:
        params: dict[str, Any] = {
            "sort": "created",
            "limit": PAGE_LIMIT,
            "include": "orderItems,moneyLogs",
        }
        date_filter = self._date_filter(date_from, date_to)
        if date_filter:
            params["filter"] = date_filter
        records: list[dict[str, Any]] = []
        source_url = f"{BASE_URL}/clouds"
        page = 1
        truncated = False
        while True:
            payload, source_url, pseudo, _ = await self._get(
                context, "orders", params={**params, "page": page}
            )
            batch = self._items(pseudo.sanitize(payload, profile="orders"))
            if not batch:
                break
            records.extend(batch)
            if len(records) >= MAX_RECORDS:
                records = records[:MAX_RECORDS]
                truncated = len(batch) == PAGE_LIMIT
                break
            if len(batch) < PAGE_LIMIT:
                break
            page += 1
        return records, truncated, source_url

    async def sales_summary(
        self, arguments: SalesSummaryInput, context: InvocationContext
    ) -> ToolEnvelope:
        date_from, date_to = arguments.date_from, arguments.date_to
        if not date_from and not date_to:
            today = datetime.now(self._timezone()).date()
            date_to = (today + timedelta(days=1)).isoformat()
            date_from = (today - timedelta(days=29)).isoformat()
        orders, truncated, source_url = await self._collect_orders(context, date_from, date_to)
        valid = [order for order in orders if not order.get("canceledDate")]
        canceled = [order for order in orders if order.get("canceledDate")]
        values = sorted(_number(order.get("totalValueRounded")) for order in valid)
        revenue_decimal = sum(values, Decimal("0"))
        by_type: dict[str, int] = defaultdict(int)
        product_revenue: dict[str, Decimal] = defaultdict(Decimal)
        product_quantity: dict[str, Decimal] = defaultdict(Decimal)
        vat_revenue: dict[str, Decimal] = defaultdict(Decimal)
        for order in orders:
            by_type[str(order.get("documentType", "?"))] += 1
        for order in valid:
            for item in order.get("orderItems", []) or []:
                if not isinstance(item, dict) or item.get("canceledDate"):
                    continue
                name = str(item.get("name") or "(bez názvu)")
                total = _number(item.get("totalPriceWithVat"))
                product_revenue[name] += total
                product_quantity[name] += _number(item.get("quantity"))
                vat_revenue[str(item.get("vat"))] += total
        count = len(valid)
        summary = {
            "period": {"from": date_from, "to": date_to},
            "currency": next((o.get("currency") for o in valid if o.get("currency")), None),
            "document_count": len(orders),
            "valid_count": count,
            "canceled_count": len(canceled),
            "revenue": _money(revenue_decimal),
            "average_receipt": _money(revenue_decimal / count) if count else 0.0,
            "median_receipt": _median(values),
            "max_receipt": _money(values[-1]) if values else 0.0,
            "min_receipt": _money(values[0]) if values else 0.0,
            "by_document_type": dict(by_type),
            "top_products": [
                {
                    "name": name,
                    "revenue": _money(value),
                    "quantity": float(product_quantity[name].quantize(Decimal("0.001"))),
                }
                for name, value in sorted(
                    product_revenue.items(), key=lambda pair: -pair[1]
                )[:15]
            ],
            "vat": {
                key: _money(value)
                for key, value in sorted(vat_revenue.items(), key=lambda pair: -pair[1])
            },
            "truncated": truncated,
        }
        warnings = (
            [f"Období obsahuje nejméně {MAX_RECORDS} dokladů; souhrn je z podmnožiny dat."]
            if truncated
            else []
        )
        return ToolEnvelope(
            data=summary,
            provenance=Provenance(
                source_id="dotykacka", source_url=source_url, retrieved_at=utc_now_iso()
            ),
            warnings=warnings,
        )

    async def test_connection(self, context: InvocationContext) -> dict[str, Any]:
        try:
            _, _, _, cloud_id = await self._get(context, "")
        except ConnectorError as exc:
            if exc.code == ErrorCode.CREDENTIAL_INVALID:
                raise ConnectorError(
                    ErrorCode.CREDENTIAL_INVALID,
                    "Autorizace Dotykačky vypršela nebo byla odvolána.",
                ) from exc
            raise
        return {"connected": True, "resource_id": cloud_id}


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _median(values: list[Decimal]) -> float:
    size = len(values)
    if not size:
        return 0.0
    value = values[size // 2] if size % 2 else (values[size // 2 - 1] + values[size // 2]) / 2
    return _money(value)
