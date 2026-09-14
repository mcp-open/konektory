from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

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
from openmcp_connector_runtime.provider import (
    basic_auth,
    credentials,
    private_envelope,
    segment,
    validated_origin,
)
from pydantic import BaseModel

from .schemas import EVIDENCES, Filter

_OPS = {
    "eq": "=",
    "neq": "!=",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
    "like": "like",
    "begins": "begins",
    "ends": "ends",
}
_LISTS = {
    "list_issued_invoices": "faktura-vydana",
    "list_received_orders": "objednavka-prijata",
    "list_invoice_types": "typ-faktury-vydane",
    "get_invoice_journal": "ucetni-denik",
    "list_stock_movements": "skladovy-pohyb",
    "get_stock_status": "stav-skladu-k-datu",
    "list_products": "cenik",
}
_DETAILS = {
    "get_issued_invoice": ("faktura-vydana", "invoice_id"),
    "get_received_order": ("objednavka-prijata", "order_id"),
    "get_stock_movement": ("skladovy-pohyb", "movement_id"),
    "get_product": ("cenik", "product_id"),
}
# ABRA embeds document items in `detail=full` regardless of `relations`, under an
# evidence-specific key (live shapes: invoice, received order, stock movement).
_ITEM_LISTS = re.compile(r"polozky[A-Za-z]*|skladovePolozky")
# Partner evidences name people in generic fields; the SDK cannot infer that.
_PERSONAL_FIELDS = {
    "adresar": frozenset({"kod", "nazev", "nazeva", "nazevb", "nazevc"}),
    "kontakt": frozenset({"kod", "nazev", "nazeva", "nazevb", "nazevc"}),
}
_SUM_KEY = re.compile(r"sum[A-Za-z0-9]{1,40}")
_SUM_VALUE = re.compile(r"-?[0-9]{1,15}(?:\.[0-9]{1,8})?")
_SUM_EXPONENT = re.compile(r"-?[0-9](?:\.[0-9]{1,17})?E[+-]?[0-9]{1,2}")
_CURRENCY = re.compile(r"[A-Za-z€$£¥ ]{1,8}")
_PROPERTY_FIELDS = {
    "propertyName": "name",
    "type": "type",
    "title": "title",
    "mandatory": "mandatory",
    "isWritable": "writable",
    "isSortable": "sortable",
    "inSummary": "in_summary",
    "inDetail": "in_detail",
    "maxLength": "max_length",
    "digits": "digits",
    "decimal": "decimal",
    "fkEvidencePath": "relation",
}


def invalid_payload() -> ConnectorError:
    return ConnectorError(ErrorCode.UPSTREAM_ERROR, "Poskytovatel vrátil neplatná data.")


def conditions(filters: list[dict[str, Any]]) -> list[str]:
    result = []
    for raw in filters:
        item = Filter.model_validate(raw)
        if item.op.startswith("is_"):
            result.append(f"{item.field} {item.op.replace('_', ' ')}")
            continue
        value = item.value
        if isinstance(value, str):
            literal = f"'{value}'"
        elif isinstance(value, bool):
            literal = "true" if value else "false"
        else:
            literal = str(value)
        result.append(f"{item.field} {_OPS[item.op]} {literal}")
    return result


def evidence_path(evidence: str, filters: list[str]) -> str:
    if evidence not in EVIDENCES:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Nepovolená evidence.")
    expression = "/" + quote("(" + " and ".join(filters) + ")", safe="") if filters else ""
    return f"/{evidence}{expression}"


def reference(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value", value.get("#text"))
    if not isinstance(value, str):
        raise invalid_payload()
    return value.removeprefix("code:")


def sum_value(value: Any) -> str:
    """Normalise a provider aggregate to exact decimal text; nothing else passes."""
    if isinstance(value, bool):
        raise invalid_payload()
    if type(value) in (int, float):
        if abs(value) > 2**53 - 1 or not math.isfinite(value):
            raise invalid_payload()
        return str(value)
    if not isinstance(value, str):
        raise invalid_payload()
    if _SUM_VALUE.fullmatch(value):
        return value
    # Large totals arrive as `2.709961007E7`; expand without going through float.
    if _SUM_EXPONENT.fullmatch(value):
        try:
            text = format(Decimal(value), "f")
        except InvalidOperation as exc:
            raise invalid_payload() from exc
        if _SUM_VALUE.fullmatch(text):
            return text
    raise invalid_payload()


def sum_group(group: Any) -> dict[str, str]:
    values = group.get("values") if isinstance(group, dict) else None
    if not isinstance(values, dict) or not values:
        raise invalid_payload()
    result = {}
    for key, entry in values.items():
        if not isinstance(key, str) or not _SUM_KEY.fullmatch(key) or not isinstance(entry, dict):
            raise invalid_payload()
        result[key] = sum_value(entry.get("value"))
    return result


def sum_currency(group: Any) -> str:
    values = group.get("values") if isinstance(group, dict) else None
    labels = {
        entry.get("currency")
        for entry in (values or {}).values()
        if isinstance(entry, dict) and "currency" in entry
    }
    if len(labels) != 1:
        raise invalid_payload()
    label = labels.pop()
    if not isinstance(label, str) or not _CURRENCY.fullmatch(label):
        raise invalid_payload()
    return label.strip()


def aggregate(winstrom: dict[str, Any]) -> dict[str, Any]:
    """Accept both the documented flat `$sum` keys and the live grouped shape."""
    flat = set(winstrom) & {"sumCelkem", "sumZaklad", "sumSazbaDph", "sumCelkemMen"}
    if flat:
        return {field: sum_value(winstrom[field]) for field in flat}
    groups = winstrom.get("sum")
    if not isinstance(groups, dict) or not groups:
        raise invalid_payload()
    totals: dict[str, dict[str, str]] = {}
    by_currency: list[dict[str, Any]] = []
    for name, group in groups.items():
        if not isinstance(name, str) or not _SUM_KEY.fullmatch(name):
            raise invalid_payload()
        if isinstance(group, list):
            # Per-currency breakdown (`sumDoklMen`) is a list of groups.
            if len(group) > 64:
                raise invalid_payload()
            for item in group:
                by_currency.append({"currency": sum_currency(item), "values": sum_group(item)})
            continue
        totals[name] = sum_group(group)
    if not totals and not by_currency:
        raise invalid_payload()
    return {"totals": totals, "by_currency": by_currency}


def flag(value: Any) -> bool | None:
    if value in ("true", True):
        return True
    if value in ("false", False):
        return False
    return None


def field_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """Project a `properties.json` entry onto documented, non-personal attributes."""
    name = entry.get("propertyName")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9@_-]{0,63}", name):
        raise invalid_payload()
    result: dict[str, Any] = {}
    for source, target in _PROPERTY_FIELDS.items():
        value = entry.get(source)
        if value is None or value == "":
            continue
        if target in {"mandatory", "writable", "sortable", "in_summary", "in_detail"}:
            value = flag(value)
            if value is None:
                continue
        elif target in {"max_length", "digits", "decimal"}:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}", value):
                continue
            value = int(value)
        elif not isinstance(value, str) or len(value) > 128:
            continue
        result[target] = value
    values = entry.get("values")
    options = values.get("value") if isinstance(values, dict) else None
    if isinstance(options, dict):
        options = [options]
    if isinstance(options, list):
        keys: list[str] = []
        for option in options[:64]:
            key = option.get("@key") if isinstance(option, dict) else None
            if isinstance(key, str) and len(key) <= 64:
                keys.append(key)
        if keys:
            result["values"] = keys
    return result


class AbraFlexiService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def close(self) -> None:
        pass  # Clients have invocation-local lifetime; no credentials persist here.

    @staticmethod
    def _credentials(context: InvocationContext) -> tuple[dict[str, str], str, str]:
        values = credentials(context, "abraflexi", ("api_url", "company", "username", "password"))
        origin = validated_origin(values["api_url"], suffix="flexibee.eu", port=5434)
        if not re.fullmatch(r"[a-z0-9_]{1,128}", values["company"]):
            raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatný identifikátor firmy.")
        company = segment(values["company"])
        basic_auth(values["username"], values["password"])
        return values, origin, company

    async def _fetch(
        self,
        context: InvocationContext,
        suffix: str,
        params: dict[str, Any],
    ) -> Any:
        values, origin, company = self._credentials(context)
        client = UpstreamClient(origin, transport=self.transport, max_response_bytes=1024 * 1024)
        try:
            payload, _ = await client.request_json(
                "GET",
                f"/c/{company}{suffix}",
                params=params,
                headers={
                    "Authorization": basic_auth(values["username"], values["password"]),
                    "Accept": "application/json",
                },
            )
        finally:
            await client.close()
        if not isinstance(payload, dict):
            raise invalid_payload()
        return payload

    @staticmethod
    def _winstrom(payload: Any) -> dict[str, Any]:
        winstrom = payload.get("winstrom") if isinstance(payload, dict) else None
        if not isinstance(winstrom, dict):
            raise invalid_payload()
        if "success" in winstrom and not (
            winstrom["success"] is True or winstrom["success"] == "true"
        ):
            raise invalid_payload()
        if "errors" in winstrom or "results" in winstrom:
            raise invalid_payload()
        return winstrom

    @classmethod
    def _rows(cls, payload: Any, evidence: str) -> tuple[list[dict[str, Any]], int | None]:
        winstrom = cls._winstrom(payload)
        rows = winstrom.get(evidence)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise invalid_payload()
        count = winstrom.get("@rowCount")
        if count is not None:
            if (
                isinstance(count, str)
                and count.isascii()
                and count.isdecimal()
                and len(count) <= 10
            ):
                count = int(count)
            if type(count) is not int or not 0 <= count <= 2**53 - 1:
                raise invalid_payload()
        return rows, count

    def _envelope(
        self, payload: Any, context: InvocationContext, evidence: str = ""
    ) -> ToolEnvelope:
        values, origin, company = self._credentials(context)
        return private_envelope(
            payload,
            context,
            "abraflexi",
            f"{origin}/c/{company}",
            values["pii_key"],
            "https://www.flexibee.eu",
            personal_fields=_PERSONAL_FIELDS.get(evidence, frozenset()),
        )

    async def _list(
        self,
        context: InvocationContext,
        evidence: str,
        args: dict[str, Any],
        filters: list[str],
        *,
        local_filter: bool = False,
    ) -> ToolEnvelope:
        limit, offset = args.get("limit", 50), args.get("offset", 0)
        params: dict[str, Any] = {
            "limit": limit,
            "start": offset,
            "add-row-count": "true",
            "detail": "full",
        }
        if args.get("fields"):
            params["detail"] = "custom:" + ",".join(args["fields"])
        if args.get("order"):
            params["order"] = args["order"]
        if evidence == "stav-skladu-k-datu":
            params.update({"sklad": "code:" + args["warehouse"], "datum": args["date"]})
        payload = await self._fetch(context, evidence_path(evidence, filters) + ".json", params)
        rows, total = self._rows(payload, evidence)
        if len(rows) > limit or (total is not None and rows and offset + len(rows) > total):
            raise invalid_payload()
        # Never consume rows beyond the returned page or pretend an incomplete scan
        # has a matching-record total. offset/next_offset always address upstream rows.
        more = offset + len(rows) < total if total is not None else len(rows) == limit
        if more and not rows:
            raise invalid_payload()
        shown = rows
        if local_filter:
            shown = [row for row in rows if self._matches(evidence, row, args)]
        result = self._envelope(
            {"items": shown, "total": None if local_filter else total}, context, evidence
        )
        result.data.update(
            {
                "offset": offset,
                "limit": limit,
                "scanned": len(rows),
                "next_offset": offset + len(rows) if more else None,
                "truncated": more,
            }
        )
        if local_filter:
            result.warnings.append(
                "Filtr byl vyhodnocen jen nad touto upstream stránkou; pokračujte přes next_offset."
            )
        return result

    @staticmethod
    def _matches(evidence: str, row: dict[str, Any], args: dict[str, Any]) -> bool:
        if evidence == "cenik":
            name = args.get("name_contains")
            if name and name.casefold() not in reference(row.get("nazev")).casefold():
                return False
            return not args.get("group") or reference(row.get("skupZboz")) == args["group"]
        if args.get("direction") and (
            reference(row.get("typPohybuK")) != "typPohybu." + args["direction"]
        ):
            return False
        return not args.get("warehouse") or reference(row.get("sklad")) == args["warehouse"]

    async def _detail(
        self,
        context: InvocationContext,
        evidence: str,
        record_id: str,
        args: dict[str, Any],
    ) -> ToolEnvelope:
        identifier = record_id.removeprefix("code:")
        params: dict[str, Any] = {"detail": "full"}
        if identifier.isdecimal() or identifier.startswith("ext:"):
            suffix = evidence_path(evidence, []) + "/" + segment(identifier) + ".json"
        else:
            # `/<evidence>/code:X.json` answers with a redirect the client refuses;
            # the equivalent filter path returns the record directly. RecordID
            # admits no quote, so the literal cannot break out of the expression.
            suffix = evidence_path(evidence, [f"kod='{identifier}'"]) + ".json"
            params["limit"] = 2
        if args.get("fields"):
            params["detail"] = "custom:" + ",".join(args["fields"])
        relations = args.get("relations", ["polozky"] if args.get("include_items", True) else [])
        if relations:
            params["relations"] = ",".join(relations)
        rows, _ = self._rows(await self._fetch(context, suffix, params), evidence)
        if not rows:
            raise ConnectorError(ErrorCode.NOT_FOUND, "Záznam nebyl nalezen.")
        if len(rows) != 1:
            raise invalid_payload()
        row = rows[0]
        if "polozky" not in relations:
            # Items are embedded even without the relation; drop them so large
            # documents (stock movements with hundreds of lines) stay within limits.
            row = {key: value for key, value in row.items() if not _ITEM_LISTS.fullmatch(key)}
        return self._envelope(row, context, evidence)

    async def invoke(
        self,
        name: str,
        arguments: BaseModel,
        context: InvocationContext,
    ) -> ToolEnvelope:
        self._credentials(context)
        args = arguments.model_dump()
        if name == "list_evidences":
            items = [
                {"evidence": evidence, "area": area}
                for evidence, area in EVIDENCES.items()
                if args.get("area") in (None, area)
            ]
            return ToolEnvelope(
                data={"items": items, "total": len(items), "truncated": False},
                provenance=Provenance(
                    source_id="abraflexi",
                    source_url="https://www.flexibee.eu/api/dokumentace/",
                    retrieved_at=utc_now_iso(),
                    freshness="cached",
                ),
                warnings=["Katalog je pevný allowlist adaptéru, nikoli seznam práv instance."],
            )
        if name in {"get_company_info", "list_companies"}:
            payload = await self._fetch(context, ".json", {"detail": "full"})
            companies = payload.get("companies")
            rows = companies.get("company") if isinstance(companies, dict) else None
            # The company detail endpoint returns a single object; only the
            # server-wide listing wraps companies in a list.
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise invalid_payload()
            result = self._envelope(
                rows[0] if name == "get_company_info" else {"items": rows}, context
            )
            if name == "list_companies":
                result.warnings.append("Jen firma vázaná na instalaci; ostatní databáze se nečtou.")
            return result
        if name in _DETAILS:
            evidence, field = _DETAILS[name]
            return await self._detail(context, evidence, args[field], args)
        if name == "get_record":
            return await self._detail(context, args["evidence"], args["record_id"], args)
        filters = conditions(args.get("filters", []))
        for field, op in (("date_from", ">="), ("date_to", "<=")):
            if args.get(field):
                filters.append(f"datVyst{op}'{args[field]}'")
        if args.get("payment_status"):
            filters.append(f"stavUhrK='stavUhr.{args['payment_status']}'")
        if name == "get_invoice_journal":
            filters.append(f"idDokl={int(args['invoice_id'])}")
        if name in _LISTS or name == "list_records":
            evidence = _LISTS[name] if name in _LISTS else args["evidence"]
            if name == "list_records" and evidence == "stav-skladu-k-datu":
                raise ConnectorError(
                    ErrorCode.INVALID_INPUT, "Stav skladu k datu čtěte přes get_stock_status."
                )
            local = name == "list_products" and bool(args.get("name_contains") or args.get("group"))
            local = local or (
                name == "list_stock_movements"
                and bool(args.get("direction") or args.get("warehouse"))
            )
            return await self._list(context, evidence, args, filters, local_filter=local)
        if name in {"sum_records", "get_evidence_properties"}:
            suffix = "/$sum.json" if name == "sum_records" else "/properties.json"
            payload = await self._fetch(
                context, evidence_path(args["evidence"], filters) + suffix, {}
            )
            if name == "get_evidence_properties":
                properties = payload.get("properties")
                if (
                    not isinstance(properties, dict)
                    or properties.get("tagName") != args["evidence"]
                ):
                    raise invalid_payload()
                fields = properties.get("property")
                if not isinstance(fields, list) or not all(
                    isinstance(item, dict) for item in fields
                ):
                    raise invalid_payload()
                # Field metadata carries no personal data; the projection keeps
                # documented attributes readable in every privacy mode.
                return ToolEnvelope(
                    data={
                        "evidence": args["evidence"],
                        "fields": [field_metadata(item) for item in fields[:512]],
                    },
                    provenance=Provenance(
                        source_id="abraflexi",
                        source_url="https://www.flexibee.eu",
                        retrieved_at=utc_now_iso(),
                    ),
                    warnings=["Metadata polí evidence; hodnoty záznamů zde nejsou."],
                )
            # Aggregates are validated decimal totals without any record content,
            # so they are returned readable in every privacy mode.
            return ToolEnvelope(
                data=aggregate(self._winstrom(payload)),
                provenance=Provenance(
                    source_id="abraflexi",
                    source_url="https://www.flexibee.eu",
                    retrieved_at=utc_now_iso(),
                ),
                warnings=["Souhrn vypočtený poskytovatelem nad filtrem; bez jednotlivých dokladů."],
            )
        raise ConnectorError(ErrorCode.NOT_FOUND, "Neznámý nástroj.")

    async def test_connection(self, context: InvocationContext) -> dict[str, bool]:
        # Company metadata needs no module permission: a read-only user without
        # the price-list module must still pass the safe test.
        payload = await self._fetch(context, ".json", {"detail": "id"})
        companies = payload.get("companies") if isinstance(payload, dict) else None
        if not isinstance(companies, dict) or "company" not in companies:
            raise invalid_payload()
        return {"connected": True}
