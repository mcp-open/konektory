"""Networkless container entrypoint smoke: health and unauthenticated dispatch denial."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


def main() -> None:
    slug = sys.argv[1]
    # CI matrix is registry-derived; this in-image helper only needs a safe
    # module identifier and verifies the runtime's reported identity below.
    if re.fullmatch(r"[a-z][a-z0-9]{1,31}", slug) is None:
        raise SystemExit("Invalid connector slug")
    if os.getuid() != 10001:
        raise SystemExit("Runtime must be non-root UID 10001")
    env = os.environ | {
        "APP_ENV": "test",
        "OPENMCP_REPLAY_STORE": "memory",
        "OPENMCP_INTERNAL_TOKEN": "synthetic-image-smoke-signing-key-0123456789abcdef",
        "OPENMCP_HTTP_ADDR": "127.0.0.1:8199",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", f"connector_{slug}"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise SystemExit("Runtime exited before readiness")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8199/health/ready", timeout=1) as res:
                    assert res.status == 200
                break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise SystemExit("Runtime readiness timed out") from None
                time.sleep(0.1)
        with urllib.request.urlopen("http://127.0.0.1:8199/version", timeout=1) as res:
            version = json.load(res)
            assert version["connector"] == slug
        request = urllib.request.Request(
            "http://127.0.0.1:8199/internal/v1/invoke",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=1)
            raise SystemExit("Unauthenticated invocation was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        print(f"{slug}: non-root image boot/readiness/version/auth-deny PASS")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
