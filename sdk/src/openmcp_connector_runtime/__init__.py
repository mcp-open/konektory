"""Stable, intentionally small runtime contract for MVP connector containers."""

from .app import ConnectorDefinition, ToolSpec, create_app
from .errors import ConnectorError, ErrorCode
from .local_cli import run_connector_main, run_local_cli
from .mcp_stdio import run_mcp_stdio
from .models import InvocationContext, Provenance, ToolEnvelope, utc_now_iso
from .provider import PrivacyMode
from .replay import (
    InMemoryReplayStore,
    ReplayStore,
    ReplayStoreUnavailable,
    ValkeyReplayStore,
)
from .runner import run
from .secrets import JsonFileSecretResolver, OpenBaoSecretResolver, SecretResolver
from .upstream import UpstreamClient

__all__ = [
    "ConnectorDefinition",
    "ConnectorError",
    "ErrorCode",
    "InvocationContext",
    "InMemoryReplayStore",
    "JsonFileSecretResolver",
    "OpenBaoSecretResolver",
    "Provenance",
    "PrivacyMode",
    "ReplayStore",
    "ReplayStoreUnavailable",
    "SecretResolver",
    "ToolEnvelope",
    "ToolSpec",
    "UpstreamClient",
    "ValkeyReplayStore",
    "create_app",
    "run",
    "run_connector_main",
    "run_local_cli",
    "run_mcp_stdio",
    "utc_now_iso",
]
