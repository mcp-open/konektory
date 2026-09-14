"""Mandatory tenant-bound PII pseudonymization for Dotykačka payloads."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Literal

from openmcp_connector_runtime import ConnectorError, ErrorCode, InvocationContext, PrivacyMode

FIELD_CATEGORY = {
    "email": "EMAIL",
    "email2": "EMAIL",
    "phone": "PHONE",
    "phone2": "PHONE",
    "tel": "PHONE",
    "mobile": "PHONE",
    "fax": "PHONE",
    "street": "ADDR",
    "city": "ADDR",
    "zip": "ADDR",
    "zipcode": "ADDR",
    "postalcode": "ADDR",
    "province": "ADDR",
    "gps": "GEO",
    "lat": "GEO",
    "lng": "GEO",
    "companyid": "REGNUM",
    "ico": "REGNUM",
    "regid": "REGNUM",
    "vatid": "TAXNUM",
    "dic": "TAXNUM",
    "taxid": "TAXNUM",
    "bankaccount": "BANK",
    "iban": "BANK",
    "birthday": "BIRTHDAY",
    "birthdate": "BIRTHDAY",
}
NAME_FIELDS = {"firstname", "lastname", "displayname", "contactname", "fullname"}
PERSON_FIELDS = {
    "buyer",
    "buyers",
    "client",
    "clients",
    "consumer",
    "consumers",
    "contact",
    "contacts",
    "customer",
    "customers",
    "employee",
    "employees",
    "guest",
    "guests",
    "operator",
    "seller",
    "user",
    "owner",
    "purchaser",
    "purchasers",
    "recipient",
    "recipients",
}
PERSON_ROLE_WORDS = frozenset(PERSON_FIELDS)
PERSON_METRIC_WORDS = {
    "amount",
    "average",
    "count",
    "maximum",
    "median",
    "minimum",
    "quantity",
    "revenue",
    "total",
    "value",
}
ADDRESS_FIELDS = {
    "address",
    "addresses",
    "billingaddress",
    "deliveryaddress",
    "destination",
    "domicile",
    "invoiceaddress",
    "postaladdress",
    "residence",
    "shippingaddress",
}
FREETEXT_FIELDS = {"note", "notes", "description", "text"}
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{6,}\d)(?!\w)")
URL_RE = re.compile(r"https?://[^\s<]+", re.IGNORECASE)
CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

SanitizationProfile = Literal["strict", "cloud", "orders"]
SAFE_ORDER_ITEM_NAMES = {"itemname", "name", "productname"}
SAFE_CLOUD_PATHS = {
    ("id",),
    ("name",),
    ("data", "id"),
    ("data", "name"),
}
SAFE_ORDER_FIELDS = {
    "canceleddate",
    "created",
    "currency",
    "customercount",
    "documenttype",
    "id",
    "totalvalue",
    "totalvaluerounded",
}
SAFE_ORDER_ITEM_FIELDS = {
    "canceleddate",
    "id",
    "itemname",
    "name",
    "productname",
    "quantity",
    "totalpricewithvat",
    "vat",
}


def derive_tenant_key(root_key: str, context: InvocationContext, cloud_id: str) -> bytes:
    if len(root_key.encode()) < 32:
        raise ConnectorError(ErrorCode.INTERNAL, "PII ochrana není správně nakonfigurována.")
    scope = "\0".join(
        (context.subject, context.workspace_id, context.installation_id, cloud_id)
    ).encode()
    return hmac.new(root_key.encode(), scope, hashlib.sha256).digest()


class Pseudonymizer:
    def __init__(
        self,
        key: bytes,
        *,
        mode: PrivacyMode = PrivacyMode.STRICT,
        secret_values: frozenset[str] = frozenset(),
    ) -> None:
        if len(key) < 32:
            raise ConnectorError(ErrorCode.INTERNAL, "PII ochrana není správně nakonfigurována.")
        self._key = key
        self._mode = mode
        self._secret_values = secret_values

    def token(self, category: str, value: object) -> str:
        digest = hmac.new(
            self._key,
            f"{category}\0{value}".encode("utf-8", errors="replace"),
            hashlib.sha256,
        ).hexdigest()[:12]
        return f"<{category}_{digest}>"

    def sanitize(
        self,
        value: Any,
        *,
        person_scope: bool = False,
        profile: SanitizationProfile = "strict",
        _address_scope: bool = False,
        _path: tuple[str, ...] = (),
    ) -> Any:
        if isinstance(value, dict):
            output: dict[Any, Any] = {}
            for key, item in value.items():
                normalized = self._normalize_key(key)
                words = self._key_words(key)
                safe_key = key
                if isinstance(key, str) and any(
                    secret and secret in key for secret in self._secret_values
                ):
                    safe_key = self.token("KEY", key)
                child_path = (*_path, normalized)
                child_person = person_scope or self._is_person_scope(normalized, words)
                child_address = _address_scope or self._is_address_scope(normalized, words)
                category = FIELD_CATEGORY.get(normalized)
                if (
                    self._mode is not PrivacyMode.PLAIN
                    and category is not None
                    and item not in (None, "")
                ):
                    output[safe_key] = self.token(category, item)
                elif (
                    self._mode is not PrivacyMode.PLAIN
                    and self._is_name_key(normalized, words)
                    and (child_person or not self._safe_name_path(profile, child_path))
                ) and item not in (None, ""):
                    output[safe_key] = self.token("NAME", item)
                elif (
                    self._mode is not PrivacyMode.PLAIN
                    and normalized in FREETEXT_FIELDS
                    and item not in (None, "")
                ):
                    # Names and physical addresses cannot be recognized
                    # reliably inside prose. Whole-field pseudonymization is
                    # the only fail-closed treatment for provider free text.
                    output[safe_key] = self.token("TEXT", item)
                else:
                    output[safe_key] = self.sanitize(
                        item,
                        person_scope=child_person,
                        profile=profile,
                        _address_scope=child_address,
                        _path=child_path,
                    )
            return output
        if isinstance(value, list):
            return [
                self.sanitize(
                    item,
                    person_scope=person_scope,
                    profile=profile,
                    _address_scope=_address_scope,
                    _path=_path,
                )
                for item in value
            ]
        if isinstance(value, tuple):
            return [
                self.sanitize(
                    item,
                    person_scope=person_scope,
                    profile=profile,
                    _address_scope=_address_scope,
                    _path=_path,
                )
                for item in value
            ]
        if isinstance(value, str):
            scrubbed_secret = self._scrub_secrets(value)
            if scrubbed_secret != value:
                if self._mode is PrivacyMode.PLAIN or self._mode is PrivacyMode.BALANCED:
                    value = scrubbed_secret
                else:
                    return self.token("SECRET", value)
        if self._mode is PrivacyMode.PLAIN:
            return value
        if person_scope and value not in (None, ""):
            # Provider schema extensions below a person object are PII by
            # default. Keeping the field shape is useful, but an unknown
            # scalar must never cross the connector boundary in plaintext.
            return self.token("PERSON", value)
        if _address_scope and value not in (None, ""):
            # Address objects are treated like person objects: an upstream
            # alias such as line1/unit must not bypass the explicit field map.
            return self.token("ADDR", value)
        if value in (None, ""):
            return value
        if self._safe_scalar_path(profile, _path):
            return self._scrub_patterns(value) if isinstance(value, str) else value
        if self._mode is PrivacyMode.BALANCED:
            return self._scrub_patterns(value) if isinstance(value, str) else value
        # Provider schema drift is untrusted. Every scalar path that is not a
        # reviewed business field or an explicit PII/free-text category is
        # pseudonymized, including numbers and booleans.
        return self.token("FIELD", value)

    @staticmethod
    def _normalize_key(key: object) -> str:
        return NON_ALNUM_RE.sub("", str(key).casefold())

    @staticmethod
    def _key_words(key: object) -> frozenset[str]:
        separated = CAMEL_BOUNDARY_RE.sub(" ", str(key))
        return frozenset(part for part in NON_ALNUM_RE.split(separated.casefold()) if part)

    @staticmethod
    def _is_name_key(normalized: str, words: frozenset[str]) -> bool:
        return normalized in NAME_FIELDS or "name" in words

    @staticmethod
    def _is_person_scope(normalized: str, words: frozenset[str]) -> bool:
        if normalized in PERSON_FIELDS:
            return True
        # Composite provider aliases (buyerDetails, customerId) are sensitive,
        # while aggregate sales metrics such as customerCount remain useful.
        return bool(words & PERSON_ROLE_WORDS) and not bool(words & PERSON_METRIC_WORDS)

    @staticmethod
    def _is_address_scope(normalized: str, words: frozenset[str]) -> bool:
        if normalized in ADDRESS_FIELDS:
            return True
        return "address" in words and not bool(words & PERSON_METRIC_WORDS)

    @staticmethod
    def _safe_name_path(profile: SanitizationProfile, path: tuple[str, ...]) -> bool:
        if profile == "cloud":
            return path in {("name",), ("data", "name")}
        if profile == "orders" and len(path) >= 2:
            return path[-2] == "orderitems" and path[-1] in SAFE_ORDER_ITEM_NAMES
        return False

    @staticmethod
    def _safe_scalar_path(profile: SanitizationProfile, path: tuple[str, ...]) -> bool:
        if profile == "cloud":
            return path in SAFE_CLOUD_PATHS
        if profile != "orders" or not path:
            return False
        if len(path) == 1:
            return path[-1] in SAFE_ORDER_FIELDS
        if path[-2] == "data":
            return path[-1] in SAFE_ORDER_FIELDS
        if path[-2] == "orderitems":
            return path[-1] in SAFE_ORDER_ITEM_FIELDS
        return False

    def _scrub_patterns(self, value: str) -> str:
        result = EMAIL_RE.sub(lambda match: self.token("EMAIL", match.group(0)), value)
        result = PHONE_RE.sub(lambda match: self.token("PHONE", match.group(0)), result)
        return URL_RE.sub(lambda match: self.token("URL", match.group(0)), result)

    def _scrub_secrets(self, value: str) -> str:
        result = value
        for secret in self._secret_values:
            if secret and secret in result:
                result = result.replace(secret, self.token("SECRET", secret))
        return result
