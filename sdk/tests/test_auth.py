from __future__ import annotations

import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from openmcp_connector_runtime.auth import (
    InvocationBinding,
    InvocationTokenVerifier,
    ReplayCache,
    TokenValidationError,
    extract_bearer,
    loads_json_no_duplicates,
)

KEY = "test-internal-token-that-is-long-enough"
NOW = 1_788_000_000
BODY = b'{"request_id":"req-1","subject":"user-1","workspace_id":"ws-1"}'


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _json_segment(value: object) -> str:
    return _encode(json.dumps(value, separators=(",", ":")).encode())


def claims(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "iss": "openmcp-core",
        "aud": "connector:demo",
        "iat": NOW,
        "exp": NOW + 45,
        "jti": _encode(b"0123456789abcdef01"),
        "request_id": "req-1",
        "subject": "user-1",
        "workspace_id": "ws-1",
        "installation_id": "inst-1",
        "connector": "demo",
        "manifest_version": "1.0.0",
        "tool": "echo",
        "body_sha256": _encode(hashlib.sha256(BODY).digest()),
    }
    result.update(overrides)
    return result


def sign(
    token_claims: dict[str, Any] | None = None,
    *,
    key: str = KEY,
    header: dict[str, Any] | None = None,
    encoded_header: str | None = None,
    encoded_payload: str | None = None,
) -> str:
    first = encoded_header or _json_segment(
        header if header is not None else {"alg": "HS256", "typ": "OMCP-INV", "v": 1}
    )
    second = encoded_payload or _json_segment(token_claims or claims())
    signing_input = f"{first}.{second}"
    signature = hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_encode(signature)}"


def binding(**overrides: str) -> InvocationBinding:
    values = {
        "request_id": "req-1",
        "subject": "user-1",
        "workspace_id": "ws-1",
        "installation_id": "inst-1",
        "connector": "demo",
        "manifest_version": "1.0.0",
        "tool": "echo",
    }
    values.update(overrides)
    return InvocationBinding(**values)


def verifier(*, cache: ReplayCache | None = None) -> InvocationTokenVerifier:
    return InvocationTokenVerifier(
        KEY,
        connector="demo",
        manifest_version="1.0.0",
        replay_store=cache or ReplayCache(),
        clock=lambda: NOW,
    )


def test_valid_token_returns_exact_claims() -> None:
    parsed = verifier().verify(sign(), binding(), BODY)
    assert parsed.audience == "connector:demo"
    assert parsed.tool == "echo"
    assert parsed.workspace_id == "ws-1"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "one.two",
        "one.two.three.four",
        "=.e30.signature",
        "*.e30.signature",
        "e30.e30.not-a-valid-signature",
        "x" * 8_193,
    ],
)
def test_rejects_malformed_compact_tokens(token: str) -> None:
    with pytest.raises(TokenValidationError):
        verifier().verify(token, binding(), BODY)


def test_rejects_duplicate_json_members_even_with_valid_signature() -> None:
    duplicate_header = _encode(b'{"alg":"HS256","alg":"HS256","typ":"OMCP-INV","v":1}')
    payload = _json_segment(claims())
    with pytest.raises(TokenValidationError):
        verifier().verify(
            sign(encoded_header=duplicate_header, encoded_payload=payload), binding(), BODY
        )

    base = claims()
    duplicate_payload_json = json.dumps(base, separators=(",", ":"))[:-1] + ',"tool":"echo"}'
    with pytest.raises(TokenValidationError):
        verifier().verify(
            sign(encoded_payload=_encode(duplicate_payload_json.encode())), binding(), BODY
        )


def test_rejects_duplicate_members_in_request_json() -> None:
    with pytest.raises(TokenValidationError):
        loads_json_no_duplicates(b'{"workspace_id":"one","workspace_id":"two"}')


@pytest.mark.parametrize("raw", [b"NaN", b"Infinity", b"-Infinity", b'{"value":NaN}'])
def test_rejects_non_standard_json_constants(raw: bytes) -> None:
    with pytest.raises(TokenValidationError):
        loads_json_no_duplicates(raw)


@pytest.mark.parametrize(
    "changed_claims",
    [
        {"iat": NOW + 1},
        {"exp": NOW},
        {"exp": NOW + 61},
        {"exp": NOW - 1},
        {"iat": True},
        {"iss": "other-core"},
        {"aud": "connector:other"},
        {"tool": "other"},
        {"workspace_id": "other-workspace"},
        {"installation_id": "other-installation"},
        {"subject": "other-user"},
        {"connector": "other"},
        {"manifest_version": "2.0.0"},
        {"jti": "short"},
    ],
)
def test_rejects_invalid_lifetime_identity_and_tenant_claims(
    changed_claims: dict[str, Any],
) -> None:
    with pytest.raises(TokenValidationError):
        verifier().verify(sign(claims(**changed_claims)), binding(), BODY)


@pytest.mark.parametrize(
    "changed_binding",
    [
        {"request_id": "other-request"},
        {"subject": "other-user"},
        {"workspace_id": "other-workspace"},
        {"installation_id": "other-installation"},
        {"connector": "other"},
        {"manifest_version": "2.0.0"},
        {"tool": "other"},
    ],
)
def test_rejects_body_binding_mismatch(changed_binding: dict[str, str]) -> None:
    with pytest.raises(TokenValidationError):
        verifier().verify(sign(), binding(**changed_binding), BODY)


def test_rejects_wrong_signature_and_raw_signing_key() -> None:
    with pytest.raises(TokenValidationError):
        verifier().verify(sign(key="different-signing-key-that-is-long-enough"), binding(), BODY)
    with pytest.raises(TokenValidationError):
        verifier().verify(KEY, binding(), BODY)


def test_rejects_body_tampering_before_replay_consumption() -> None:
    shared_verifier = verifier()
    token = sign()
    with pytest.raises(TokenValidationError):
        shared_verifier.verify(token, binding(), BODY + b" ")
    # A failed digest check does not consume the JTI; the exact body can still run once.
    shared_verifier.verify(token, binding(), BODY)


def test_token_is_exactly_one_use_under_concurrency() -> None:
    shared_verifier = verifier()
    token = sign()

    def verify_once(_: int) -> bool:
        try:
            shared_verifier.verify(token, binding(), BODY)
        except TokenValidationError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(verify_once, range(64)))
    assert results.count(True) == 1


def test_replay_cache_is_bounded_and_fails_closed() -> None:
    shared_verifier = verifier(cache=ReplayCache(max_entries=1))
    shared_verifier.verify(sign(claims(jti=_encode(b"first-jti-12345678"))), binding(), BODY)
    second = sign(claims(jti=_encode(b"second-jti-1234567")))
    with pytest.raises(TokenValidationError):
        shared_verifier.verify(second, binding(), BODY)


def test_authorization_header_is_singular_and_strict() -> None:
    assert extract_bearer([(b"authorization", b"Bearer abc")]) == "abc"
    for headers in (
        [],
        [(b"authorization", b"Basic abc")],
        [(b"authorization", b"Bearer  abc")],
        [(b"authorization", b"Bearer abc"), (b"Authorization", b"Bearer abc")],
    ):
        with pytest.raises(TokenValidationError):
            extract_bearer(headers)


def test_go_generated_cross_language_fixture() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "invocation_hs256_v1.json"
    fixture = json.loads(fixture_path.read_text())
    fixed_verifier = InvocationTokenVerifier(
        fixture["signing_key"],
        connector=fixture["claims"]["connector"],
        manifest_version=fixture["claims"]["manifest_version"],
        replay_store=ReplayCache(),
        clock=lambda: fixture["verification_time"],
    )
    parsed = fixed_verifier.verify(
        fixture["token"],
        InvocationBinding(
            request_id=fixture["claims"]["request_id"],
            subject=fixture["claims"]["subject"],
            workspace_id=fixture["claims"]["workspace_id"],
            installation_id=fixture["claims"]["installation_id"],
            connector=fixture["claims"]["connector"],
            manifest_version=fixture["claims"]["manifest_version"],
            tool=fixture["claims"]["tool"],
        ),
        fixture["body_json"].encode(),
    )
    assert parsed.jti == fixture["claims"]["jti"]

    # Development check prevents the Docker-context copies from drifting while
    # each language can still run in its isolated production build context.
    backend_fixture = (
        Path(__file__).parents[3]
        / "backend"
        / "internal"
        / "connectors"
        / "testdata"
        / "invocation_hs256_v1.json"
    )
    if backend_fixture.exists():
        assert json.loads(backend_fixture.read_text()) == fixture
