"""Run connector checks from the registry inside the pinned test container."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from connector_inventory import load_connectors

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connector", help="Run SDK and one registered adapter during development")
    args = parser.parse_args()
    connectors = load_connectors(ROOT)
    if args.connector is not None and args.connector not in connectors:
        parser.error("Connector must be registered")
    # Discover sources from reviewed registry entries, not a duplicated image ENV.
    packages = ["sdk", *connectors]
    env = os.environ | {
        "PYTHONPATH": os.pathsep.join([str(ROOT), *(str(ROOT / p / "src") for p in packages)]),
        "RUFF_CACHE_DIR": "/tmp/ruff",
        "MYPY_CACHE_DIR": "/tmp/mypy",
    }

    def run(*command: str) -> None:
        subprocess.run(command, cwd=ROOT, env=env, check=True)

    run(sys.executable, "scripts/connector_inventory.py", "check")
    selected = ["sdk", args.connector] if args.connector else packages
    for package in selected:
        run("ruff", "check", f"{package}/src", f"{package}/tests")
        run("mypy", "--config-file", f"{package}/pyproject.toml", f"{package}/src")
        run("pytest", "-q", "-W", "error", "--strict-markers", "-o",
            f"cache_dir=/tmp/pytest/{package}", f"{package}/tests")
    if args.connector is None:
        run("ruff", "check", "--config", "sdk/pyproject.toml", "tests", "scripts")
        run("pytest", "-q", "-W", "error", "--strict-markers", "-o",
            "cache_dir=/tmp/pytest/root", "tests")
        run(sys.executable, "scripts/manifests.py")


if __name__ == "__main__":
    main()
