from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from openmcp_connector_runtime import (
    ConnectorDefinition,
    InMemoryReplayStore,
    ToolSpec,
    create_app,
)
from openmcp_connector_runtime.signing_secret import invocation_signing_key_from_env

SECRET = "connector-signing-secret-with-32-plus-bytes"


def test_inline_and_file_sources_are_strict_xor(tmp_path: Path) -> None:
    secret_file = tmp_path / "signing-key"
    secret_file.write_text(SECRET, encoding="utf-8")

    assert invocation_signing_key_from_env(environ={"OPENMCP_INTERNAL_TOKEN": SECRET}) == SECRET
    assert (
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(secret_file)}
        )
        == SECRET
    )
    with pytest.raises(RuntimeError, match="configuration is invalid"):
        invocation_signing_key_from_env(environ={})
    with pytest.raises(RuntimeError, match="configuration is invalid"):
        invocation_signing_key_from_env(
            environ={
                "OPENMCP_INTERNAL_TOKEN": SECRET,
                "OPENMCP_INTERNAL_TOKEN_FILE": str(secret_file),
            }
        )


@pytest.mark.parametrize("terminator", [b"", b"\n", b"\r", b"\r\n"])
def test_file_source_trims_at_most_one_line_ending(
    tmp_path: Path, terminator: bytes
) -> None:
    secret_file = tmp_path / "signing-key"
    secret_file.write_bytes(SECRET.encode("utf-8") + terminator)

    assert (
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(secret_file)}
        )
        == SECRET
    )


@pytest.mark.parametrize(
    "invalid_suffix",
    [b"\n\n", b"\r\r", b"\r\n\n", b"\nsecond-line", b"\x00"],
)
def test_file_source_rejects_embedded_or_repeated_line_endings_and_nul(
    tmp_path: Path, invalid_suffix: bytes
) -> None:
    secret_file = tmp_path / "signing-key"
    secret_file.write_bytes(SECRET.encode("utf-8") + invalid_suffix)

    with pytest.raises(RuntimeError, match="secret is invalid"):
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(secret_file)}
        )


@pytest.mark.parametrize("invalid_suffix", ["\n", "\r", "\x00"])
def test_inline_source_rejects_line_endings_and_nul(invalid_suffix: str) -> None:
    with pytest.raises(RuntimeError, match="secret is invalid"):
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN": SECRET + invalid_suffix}
        )


def test_file_source_requires_absolute_regular_non_symlink_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "signing-key"
    secret_file.write_text(SECRET, encoding="utf-8")
    link = tmp_path / "signing-key-link"
    link.symlink_to(secret_file)

    for invalid_path in ("relative-key", str(tmp_path), str(link)):
        with pytest.raises(RuntimeError, match="configuration is invalid") as captured:
            invocation_signing_key_from_env(
                environ={"OPENMCP_INTERNAL_TOKEN_FILE": invalid_path}
            )
        assert invalid_path not in str(captured.value)


def test_file_source_rejects_fifo_without_opening_it(tmp_path: Path) -> None:
    fifo = tmp_path / "signing-key-fifo"
    os.mkfifo(fifo)

    with pytest.raises(RuntimeError, match="configuration is invalid"):
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(fifo)}
        )


def test_file_source_is_bounded_and_rejects_invalid_utf8(tmp_path: Path) -> None:
    secret_file = tmp_path / "signing-key"
    secret_file.write_bytes(b"a" * 4_096)
    assert (
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(secret_file)}
        )
        == "a" * 4_096
    )

    secret_file.write_bytes(b"a" * 4_097)
    with pytest.raises(RuntimeError, match="secret is invalid"):
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(secret_file)}
        )

    secret_file.write_bytes(b"a" * 32 + b"\xff")
    with pytest.raises(RuntimeError, match="secret is invalid"):
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(secret_file)}
        )


def test_errors_never_expose_secret_or_file_path(tmp_path: Path) -> None:
    sensitive_value = "too-short-private-value"
    sensitive_path = tmp_path / "missing-private-signing-key"

    with pytest.raises(RuntimeError) as inline_error:
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN": sensitive_value}
        )
    assert sensitive_value not in str(inline_error.value)

    with pytest.raises(RuntimeError) as file_error:
        invocation_signing_key_from_env(
            environ={"OPENMCP_INTERNAL_TOKEN_FILE": str(sensitive_path)}
        )
    assert str(sensitive_path) not in str(file_error.value)


def test_runtime_uses_file_source_without_connector_specific_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Input(BaseModel):
        value: int

    async def handler(arguments: BaseModel, context: object) -> dict[str, int]:
        return {"value": Input.model_validate(arguments).value}

    secret_file = tmp_path / "signing-key"
    secret_file.write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.delenv("OPENMCP_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("OPENMCP_INTERNAL_TOKEN_FILE", str(secret_file))
    runtime = create_app(
        ConnectorDefinition(
            "sdk-contract",
            "1.0.0",
            {"echo": ToolSpec("echo", Input, handler, "Echo")},
        ),
        replay_store=InMemoryReplayStore(),
    )

    assert runtime.debug is False
