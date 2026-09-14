"""Fail-closed loading for the connector invocation signing secret."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from contextlib import suppress

_TOKEN_ENV = "OPENMCP_INTERNAL_TOKEN"
_TOKEN_FILE_ENV = "OPENMCP_INTERNAL_TOKEN_FILE"
_MAX_SECRET_FILE_BYTES = 4_096
_CONFIG_ERROR = "invocation signing secret configuration is invalid"
_SECRET_ERROR = "invocation signing secret is invalid"
_PLACEHOLDER_ERROR = "invocation signing secret must not use a public placeholder"


def _read_bounded_regular_file(path: str) -> bytes:
    if not path or "\x00" in path or not os.path.isabs(path):
        raise RuntimeError(_CONFIG_ERROR)

    descriptor = -1
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RuntimeError(_CONFIG_ERROR)
        if before.st_size > _MAX_SECRET_FILE_BYTES:
            raise RuntimeError(_SECRET_ERROR)

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise RuntimeError(_CONFIG_ERROR)

        chunks: list[bytes] = []
        total = 0
        while total <= _MAX_SECRET_FILE_BYTES:
            chunk = os.read(descriptor, min(4_096, _MAX_SECRET_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > _MAX_SECRET_FILE_BYTES:
            raise RuntimeError(_SECRET_ERROR)
        return b"".join(chunks)
    except RuntimeError:
        raise
    except (OSError, ValueError):
        raise RuntimeError(_CONFIG_ERROR) from None
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


def _validate_secret(secret: str) -> str:
    try:
        encoded = secret.encode("utf-8")
    except UnicodeError:
        raise RuntimeError(_SECRET_ERROR) from None
    if (
        len(encoded) < 32
        or len(encoded) > _MAX_SECRET_FILE_BYTES
        or any(character in secret for character in "\x00\r\n")
    ):
        raise RuntimeError(_SECRET_ERROR)
    normalized = secret.strip().lower()
    if normalized.startswith(("replace_", "change_", "changeme", "example")):
        raise RuntimeError(_PLACEHOLDER_ERROR)
    return secret


def _decode_file_secret(raw: bytes) -> str:
    # Secret mounts commonly add exactly one POSIX or Windows line ending.
    # Strip only that terminator; any remaining CR/LF is ambiguous and rejected.
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith((b"\r", b"\n")):
        raw = raw[:-1]
    if any(character in raw for character in (0, 10, 13)):
        raise RuntimeError(_SECRET_ERROR)
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        raise RuntimeError(_SECRET_ERROR) from None


def validate_invocation_signing_key(secret: str) -> str:
    """Validate an explicitly injected SDK signing key without environment lookup."""

    return _validate_secret(secret)


def invocation_signing_key_from_env(
    *, environ: Mapping[str, str] | None = None
) -> str:
    """Load exactly one signing-key source without exposing its value or path."""

    values = os.environ if environ is None else environ
    inline_configured = _TOKEN_ENV in values
    file_configured = _TOKEN_FILE_ENV in values
    if inline_configured == file_configured:
        raise RuntimeError(_CONFIG_ERROR)
    if inline_configured:
        return _validate_secret(values[_TOKEN_ENV])
    raw = _read_bounded_regular_file(values[_TOKEN_FILE_ENV])
    return _validate_secret(_decode_file_secret(raw))
