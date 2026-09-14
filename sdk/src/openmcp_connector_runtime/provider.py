"""Small, fail-closed helpers for read-only private provider adapters."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from datetime import date
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlsplit

from .errors import ConnectorError, ErrorCode
from .models import InvocationContext, Provenance, ToolEnvelope, utc_now_iso

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}")
_KEY = re.compile(r"[@A-Za-z_][@A-Za-z0-9_.:-]{0,63}")
_NUMBERS = frozenset(
    {
        "id",
        "count",
        "totalcount",
        "total",
        "totalprice",
        "totalvalue",
        "price",
        "amount",
        "quantity",
        "limit",
        "offset",
        "page",
        "pages",
        "currentpage",
        "numberofpages",
        "rowcount",
        "sumcelkem",
        "sumcelkemmen",
        "sumzaklad",
        "sumsazbadph",
        "unitprice",
        "revenue",
        "probability",
        "vat",
        "pricewithvat",
        "pricewithoutvat",
        "stock",
        "stockquantity",
        "version",
        "size",
        "ordertotal",
        "totalwithvat",
        "totalwithoutvat",
        "numberofitems",
        "currentpageitems",
        "productid",
        "statusid",
        "cenamj",
        "stavmj",
        "pricesale",
        "mnozmj",
        "sumdph",
    }
)
_BOOLS = frozenset({"success", "active", "activeyn", "paidyn", "archivedyn", "truncated"})
_DATES = frozenset(
    {
        "date",
        "datvyst",
        "datsplat",
        "creationtime",
        "lastupdatetime",
        "created",
        "createdat",
        "updatedat",
        "validfrom",
        "validtill",
        "lastupdate",
        "datuhr",
        "duzppuv",
        "duzpucto",
        "datum",
    }
)
# ISO date with an optional time and zone suffix; ABRA renders dates as
# `2025-01-01+01:00` and timestamps as `2026-01-02T15:03:58.737+01:00`.
_DATE_TEXT = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})?"
)
_PRIVATE_SCOPE = re.compile(
    r"person|contact|customer|buyer|address|employee|owner|recipient|delivery|billing|bank"
    r"|kontakt|osoba|adresa(?!r)|zakaznik|odberatel|dodavatel|prijemce|zamestnanec|uzivatel",
    re.IGNORECASE,
)
# ABRA renders relation labels as `firma@showAs`; the suffix keeps the class.
_BALANCED_PRIVATE_FIELD = re.compile(
    r"(?:(?:first|last|full|display|contact|user)name|email|e-mail|phone|mobile|fax|tel|"
    r"street|city|zip|postal|province|address|birth[a-z]*|nationality[a-z]*|sex|gender|"
    r"occupation|taxidentification[a-z]*|carregistration[a-z]*|licenseplate|passport[a-z]*|"
    r"identitycard[a-z]*|visa[a-z]*|"
    r"iban|bankaccount|accountnumber|taxid|vatid|dic|gps|latitude|longitude|lat|lng|"
    r"ssn|socialsecuritynumber|personalnumber|nationalid|identitynumber|passport|"
    r"idcard|createdby|updatedby|assignedto|salesrep|agent|ipaddress|"
    # Czech/Slovak provider field names (ABRA Flexi, Pohoda, iDoklad, SuperFaktura…).
    r"jmeno|prijmeni|titul|nazfirmy|firma|ulice|mesto|psc|obec|tel|telefon|mobil|www|"
    r"fa(?:nazev2?|ulice|mesto|psc)|rodnecislo|rc|datnar|datumnarozeni|cisloop|cislopasu|"
    r"buc|bic|specsym|ucet|cisloucetu|vytvoril|zmenil|zpracoval|odpovosoba|prevzal|schvalil|"
    r"login|username|prihlaseni)(?:showas|ref)?",
    re.IGNORECASE,
)
_BALANCED_FREE_TEXT = frozenset(
    {
        "note", "notes", "comment", "comments", "description", "message", "text",
        "memo", "remark", "remarks",
        "popis", "poznam", "poznamka", "uvodtxt", "zavtxt", "komentar", "zprava", "predmet",
        "obsah",
    }
)
_EMAIL_IN_TEXT = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
# A phone candidate never starts or ends inside an identifier, a decimal or
# a slash/hyphen-joined code such as `FV1-000002/2025`; `_looks_like_phone`
# then rejects amounts, ISO dates and short registry numbers (IČO has 8 digits).
_PHONE_IN_TEXT = re.compile(r"(?<![\w.,/-])\+?\(?\d[\d ().-]{6,18}\d(?![\w.,/-])")
_ISO_DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")
_URL_IN_TEXT = re.compile(r"https?://[^\s<]+", re.IGNORECASE)
_IBAN_IN_TEXT = re.compile(
    r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?(?![A-Z0-9])"
)


def _looks_like_phone(candidate: str) -> bool:
    digits = sum(char.isdigit() for char in candidate)
    if not 9 <= digits <= 15 or _ISO_DATE_PREFIX.match(candidate):
        return False
    if candidate.isdigit():
        return digits == 9 or (digits >= 11 and candidate.startswith(("00", "420", "421")))
    return not re.fullmatch(r"[\d.,]+", candidate)


_KEYS = (
    _NUMBERS
    | _BOOLS
    | _DATES
    | frozenset(
        {
            "data",
            "winstrom",
            "results",
            "items",
            "orders",
            "products",
            "customers",
            "invoices",
            "categories",
            "contacts",
            "companies",
            "businesscases",
            "leads",
            "records",
            "name",
            "email",
            "phone",
            "address",
            "street",
            "city",
            "zip",
            "firstname",
            "lastname",
            "customer",
            "contact",
            "owner",
            "recipient",
            "billing",
            "delivery",
            "bank",
            "iban",
            "description",
            "note",
            "notes",
            "type",
            "status",
            "code",
            "currency",
            "country",
            "countrycode",
            "ordernumber",
            "invoicenumber",
            "customerid",
            "categoryid",
            "parentid",
            "labels",
            "availabilities",
            "manufacturers",
            "parameters",
            "carts",
            "vouchers",
            "shipments",
            "payments",
            "webhooks",
            "events",
            "languages",
            "config",
            "pricelists",
            "fakturavydana",
            "objednavkaprijata",
            "adresar",
            "cenik",
            "skladovakarta",
            "skladovypohyb",
            "nastaveni",
            "firma",
            "stav",
            "kod",
            "nazev",
            "error",
            "message",
            "polozky",
            "polozkyfaktury",
            "polozkyobchdokladu",
            "skladovepolozky",
            "stavuhrk",
            "typdoklk",
            "mena",
        }
    )
)


def credentials(context: InvocationContext, slug: str, required: tuple[str, ...]) -> dict[str, str]:
    """Resolve only the exact installation's body-bound credential, never env/Bao."""
    if context.secret_ref != f"{slug}/{context.workspace_id}/{context.installation_id}":
        raise ConnectorError(ErrorCode.FORBIDDEN, "Credentials nejsou svázané s instalací.")
    if (
        context.secret_version is None
        or context.secret_version < 1
        or context.provider_credential is None
    ):
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Chybí verze credentials.")
    values = {key: value.get_secret_value() for key, value in context.provider_credential.items()}
    if any(
        not values.get(key) or len(values[key].encode()) > 4096 for key in (*required, "pii_key")
    ):
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Credentials nejsou úplné.")
    if len(values["pii_key"].encode()) < 32:
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Chybí bezpečný PII klíč.")
    return values


def basic_auth(username: str, password: str) -> str:
    if (
        not username
        or not password
        or ":" in username
        or len(username.encode()) > 4096
        or len(password.encode()) > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in username + password)
    ):
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Neplatné Basic credentials.")
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"


def validated_origin(raw: str, *, suffix: str, port: int = 443, path: str = "") -> str:
    """Only a provider-managed, single-label HTTPS tenant origin is accepted."""
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        label = hostname.removesuffix("." + suffix)
        valid = (
            raw == raw.strip()
            and raw.isascii()
            and not any(ord(char) < 32 or ord(char) == 127 for char in raw)
            and "\\" not in raw
            and parsed.scheme == "https"
            and hostname.endswith("." + suffix)
            and _LABEL.fullmatch(label) is not None
            and parsed.port in ((None, 443) if port == 443 else (port,))
            and parsed.username is None
            and parsed.password is None
            and not parsed.netloc.endswith(":")
            and not parsed.query
            and not parsed.fragment
            and parsed.path.rstrip("/") == path
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Nepovolená adresa poskytovatele.")
    return f"https://{hostname}{':' + str(port) if port != 443 else ''}{path}"


def segment(value: str | int) -> str:
    raw = str(value)
    if isinstance(value, bool) or not _SEGMENT.fullmatch(raw) or ".." in raw:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatný identifikátor záznamu.")
    return quote(raw, safe="")


class PrivacyMode(StrEnum):
    STRICT = "strict"
    BALANCED = "balanced"
    PLAIN = "plain"


def privacy_mode(context: InvocationContext) -> PrivacyMode:
    """Return the body-bound mode, defaulting old callers to the strict contract."""
    raw = context.runtime_flags.get("privacy_mode", PrivacyMode.STRICT.value)
    if not isinstance(raw, str):
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatný režim ochrany dat.")
    try:
        return PrivacyMode(raw)
    except ValueError as exc:
        raise ConnectorError(ErrorCode.INVALID_INPUT, "Neplatný režim ochrany dat.") from exc


def business_value(field: str, value: Any) -> bool:
    """True for an allowlisted metric, flag, numeric id or date that stays readable."""
    if field in _BOOLS and isinstance(value, bool):
        return True
    if (
        field in _NUMBERS
        and type(value) in (int, float)
        and (-(2**53) < value < 2**53 and math.isfinite(value))
    ):
        return True
    if not isinstance(value, str):
        return False
    if field == "id" and re.fullmatch(r"[0-9]{1,18}", value):
        return True
    if field in _NUMBERS and re.fullmatch(r"-?[0-9]{1,15}(?:\.[0-9]{1,8})?", value):
        # Providers encode money as decimal strings. Keep exact precision,
        # never round through float; the digit bound is below 2**53.
        return True
    if field in _DATES:
        match = _DATE_TEXT.fullmatch(value)
        if match is not None:
            try:
                date.fromisoformat(match.group(1))
                return True
            except ValueError:
                return False
    return False


def private_envelope(
    payload: Any,
    context: InvocationContext,
    slug: str,
    scope: str,
    pii_key: str,
    source_url: str,
    *,
    personal_fields: frozenset[str] = frozenset(),
) -> ToolEnvelope:
    """Apply the invocation-bound privacy mode and always suppress credentials.

    Strict keeps the conservative schema-drift behavior. Balanced preserves
    business fields while masking classified personal/free-text content. Plain
    preserves provider data but is not allowed to disclose credential values.
    `personal_fields` names normalised keys (lowercase alphanumerics) whose
    values are personal in this payload even though the name is generic, e.g.
    `nazev` of an address-book partner; the adapter knows the evidence, the
    SDK does not.
    """
    if len(pii_key.encode()) < 32:
        raise ConnectorError(ErrorCode.CREDENTIAL_INVALID, "Chybí bezpečný PII klíč.")
    binding = json.dumps(
        [slug, context.subject, context.workspace_id, context.installation_id, scope],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    key = hmac.digest(pii_key.encode(), binding, "sha256")
    mode = privacy_mode(context)
    secret_values = {
        value.get_secret_value() for value in (context.provider_credential or {}).values()
    }
    visited = 0

    def token(value: Any, category: str = "FIELD") -> str:
        message = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        digest = hmac.new(key, f"{category}\0{message}".encode(), hashlib.sha256).hexdigest()[:24]
        return f"<{category}_{digest}>"

    def scrub_secret_text(value: str) -> str:
        result = value
        for secret in secret_values:
            if secret and secret in result:
                result = result.replace(secret, token(secret, "SECRET"))
        return result

    def scrub(value: Any, field: str = "", private: bool = False, depth: int = 0) -> Any:
        nonlocal visited
        visited += 1
        if depth > 32 or visited > 10_000:
            raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Odpověď je příliš složitá.")
        if isinstance(value, dict):
            result = {}
            for name, item in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(name).casefold())
                key_text = str(name)
                key_contains_secret = any(
                    secret and secret in key_text for secret in secret_values
                )
                safe_name = (
                    name
                    if isinstance(name, str)
                    and not key_contains_secret
                    and (
                        mode is not PrivacyMode.STRICT
                        or (_KEY.fullmatch(name) and normalized in _KEYS)
                    )
                    and (
                        mode is PrivacyMode.PLAIN
                        or (
                            _EMAIL_IN_TEXT.fullmatch(name) is None
                            and _PHONE_IN_TEXT.fullmatch(name) is None
                        )
                    )
                    else token(str(name), "KEY")
                )
                balanced_private = bool(_PRIVATE_SCOPE.search(key_text)) or bool(
                    _BALANCED_PRIVATE_FIELD.fullmatch(normalized)
                )
                result[safe_name] = scrub(
                    item,
                    normalized,
                    private
                    or bool(_PRIVATE_SCOPE.search(key_text))
                    or normalized in personal_fields
                    or (mode is PrivacyMode.BALANCED and balanced_private),
                    depth + 1,
                )
            return result
        if isinstance(value, list):
            return [scrub(item, field, private, depth + 1) for item in value]
        if value is None or value == "":
            return value
        if isinstance(value, str):
            scrubbed_secret = scrub_secret_text(value)
            if scrubbed_secret != value:
                if mode is PrivacyMode.PLAIN or mode is PrivacyMode.BALANCED:
                    value = scrubbed_secret
                else:
                    return token(value, "SECRET")
        if mode is PrivacyMode.PLAIN:
            return value
        if mode is PrivacyMode.BALANCED and not private:
            if field in _BALANCED_FREE_TEXT:
                return token(value, "TEXT")
            # Validated metrics, dates and flags are business data in every mode;
            # they must never be mistaken for a phone number below.
            if business_value(field, value):
                return value
            if isinstance(value, str):
                scrubbed_text = _EMAIL_IN_TEXT.sub(
                    lambda match: token(match.group(0), "EMAIL"), value
                )
                scrubbed_text = _IBAN_IN_TEXT.sub(
                    lambda match: token(match.group(0), "IBAN"), scrubbed_text
                )
                scrubbed_text = _PHONE_IN_TEXT.sub(
                    lambda match: (
                        token(match.group(0), "PHONE")
                        if _looks_like_phone(match.group(0))
                        else match.group(0)
                    ),
                    scrubbed_text,
                )
                return _URL_IN_TEXT.sub(
                    lambda match: token(match.group(0), "URL"), scrubbed_text
                )
            return value
        if not private and business_value(field, value):
            return value
        return token(value)

    result = ToolEnvelope(
        data=scrub(payload),
        provenance=Provenance(source_id=slug, source_url=source_url, retrieved_at=utc_now_iso()),
        warnings=[
            "Výstup bez pseudonymizace; může obsahovat osobní údaje providera."
            if mode is PrivacyMode.PLAIN
            else (
                "Obchodní data jsou čitelná; osobní údaje a volný text jsou "
                "pseudonymizovány; nejde o anonymní data."
                if mode is PrivacyMode.BALANCED
                else "Osobní a neznámá pole jsou pseudonymizována; nejde o anonymní data."
            )
        ],
    )
    if len(result.model_dump_json().encode()) > 256 * 1024:
        raise ConnectorError(ErrorCode.UPSTREAM_ERROR, "Výstup je příliš velký; zúžte dotaz.")
    return result
