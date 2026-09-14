#!/usr/bin/env python3
"""Print an MCP client configuration snippet for one connector (Docker or Python).

    python3 scripts/mcp_config.py freelo --credentials ~/.openmcp/freelo.json
    python3 scripts/mcp_config.py freelo --credentials ~/.openmcp/freelo.json --python
    python3 scripts/mcp_config.py ares --claude-code

The snippet never contains secrets: only the path of the owner-only
credentials file is referenced.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from connector_inventory import load_connectors  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("slug")
    parser.add_argument("--credentials", help="absolute path to the owner-only credentials JSON")
    parser.add_argument("--image", help="image tag; default openmcp-connector-<slug>:local")
    parser.add_argument(
        "--python", action="store_true", help="run the installed package instead of Docker"
    )
    parser.add_argument("--plain", action="store_true", help="add --plain (no pseudonymisation)")
    parser.add_argument(
        "--claude-code", action="store_true", help="print a `claude mcp add-json` command"
    )
    args = parser.parse_args()

    connectors = load_connectors(ROOT)
    if args.slug not in connectors:
        parser.error(f"unknown connector {args.slug!r}; registered: {', '.join(connectors)}")
    needs_credentials = args.slug != "ares"
    if needs_credentials and not args.credentials:
        parser.error("--credentials is required for private connectors")
    credentials = (
        os.path.abspath(os.path.expanduser(args.credentials)) if args.credentials else None
    )
    if credentials and not os.path.isabs(credentials):
        parser.error("--credentials must be an absolute path")

    server_args: list[str]
    env: dict[str, str] = {}
    if args.python:
        command = sys.executable
        server_args = ["-m", f"connector_{args.slug}", "mcp"]
        if credentials:
            env["OPENMCP_LOCAL_CREDENTIALS_FILE"] = credentials
    else:
        command = "docker"
        server_args = ["run", "-i", "--rm", "--network", "bridge"]
        if credentials:
            try:
                uid, gid = os.getuid(), os.getgid()
            except AttributeError:  # Windows: image default user
                uid = gid = None
            if uid is not None:
                server_args += ["--user", f"{uid}:{gid}"]
            server_args += [
                "-e", "OPENMCP_LOCAL_CREDENTIALS_FILE=/run/openmcp/credentials.json",
                "-v", f"{credentials}:/run/openmcp/credentials.json:ro",
            ]
        server_args += [args.image or f"openmcp-connector-{args.slug}:local", "mcp"]
    if args.plain:
        server_args.append("--plain")

    entry = {"command": command, "args": server_args}
    if env:
        entry["env"] = env
    name = f"openmcp-{args.slug}"
    if args.claude_code:
        print(f"claude mcp add-json {name} '{json.dumps(entry, ensure_ascii=False)}'")
    else:
        print(json.dumps({"mcpServers": {name: entry}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
