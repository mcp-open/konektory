"""Uvicorn launcher with a Compose/k3s-compatible address contract."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

import uvicorn
from starlette.applications import Starlette

_LOG_FIELDS = ("connector", "tool", "request_id", "outcome", "provider_status", "duration_ms")


class _StructuredFormatter(logging.Formatter):
    """One JSON object per line with only the bounded, non-sensitive extras."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        for field in _LOG_FIELDS:
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    """Emit connector invocation/test outcomes to stdout; idempotent."""
    logger = logging.getLogger("openmcp.connector")
    if any(getattr(handler, "_openmcp_structured", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter())
    setattr(handler, "_openmcp_structured", True)  # noqa: B010
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def run(app: Starlette, *, default_port: int) -> None:
    configure_logging()
    raw = os.environ.get("OPENMCP_HTTP_ADDR")
    if raw:
        if raw.startswith(":"):
            host, port_text = "0.0.0.0", raw[1:]
        elif ":" in raw:
            host, port_text = raw.rsplit(":", 1)
        else:
            raise RuntimeError("OPENMCP_HTTP_ADDR must be :port or host:port")
    else:
        host = "0.0.0.0"
        port_text = os.environ.get("PORT", str(default_port))
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("connector port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("connector port must be between 1 and 65535")
    uvicorn.run(app, host=host or "0.0.0.0", port=port, access_log=False)
