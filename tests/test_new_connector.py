"""Scaffolder checks: input validation, deterministic render, transactional publish.

The generated package is also imported and its own lint/type/test gates are run
in a disposable directory with synthetic credentials and signed invocations.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "new_connector.py"
# Existing port whose Dockerfile/pyproject pins a new scaffold must reproduce.
REFERENCE = "raynet"
SLUG = "mujprovider"
MODULE = f"connector_{SLUG}"
NAME = "Můj provider"
PORT = 8106
SHARED_FILES = {
    "connectors.list": "ares\n",
    "Makefile": "CONNECTORS := $(shell cat connectors.list)\n",
    "scripts/manifests.py": "PROVIDERS = {}\n",
}


def load_scaffolder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("new_connector_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


nc = load_scaffolder()


def snapshot(root: Path) -> dict[str, bytes]:
    """Every entry below ``root`` with its content, type or symlink target."""

    entries: dict[str, bytes] = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            path = base / name
            key = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries[key] = b"symlink:" + os.readlink(path).encode()
            elif path.is_dir():
                entries[key] = b"dir"
            else:
                entries[key] = path.read_bytes()
    return entries


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Disposable repository root with real SDK pins, registry/CI sentinels and a sibling."""

    root = tmp_path / "repo"
    for directory in ("sdk", "scripts", "legacy"):
        (root / directory).mkdir(parents=True)
    shutil.copyfile(ROOT / "sdk" / "pyproject.toml", root / "sdk" / "pyproject.toml")
    shutil.copyfile(ROOT / "scripts" / "Dockerfile.test", root / "scripts" / "Dockerfile.test")
    for relative, content in SHARED_FILES.items():
        (root / relative).write_text(content, encoding="utf-8")
    (root / "legacy" / "Dockerfile").write_text("FROM scratch\nEXPOSE 8150\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "a",
        "A",
        "Ab",
        "ab-c",
        "ab_c",
        "1ab",
        "../x",
        "a/b",
        "a b",
        "ab.",
        "x" * 33,
        "sdk",
        "tests",
        "scripts",
        "connector",
        None,
        7,
    ],
)
def test_invalid_slugs_are_rejected_without_writing(repo: Path, slug: Any) -> None:
    before = snapshot(repo)
    with pytest.raises(nc.ScaffoldError):
        nc.scaffold(repo, slug, NAME, PORT)
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    "port",
    [8105, 9000, 0, -1, 65536, "abc", "8106 ", " 8106", "", True, None, 8106.0, 8150],
)
def test_invalid_or_taken_ports_are_rejected_without_writing(repo: Path, port: Any) -> None:
    before = snapshot(repo)
    with pytest.raises(nc.ScaffoldError):
        nc.scaffold(repo, SLUG, NAME, port)
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    "name",
    [
        "",
        " X",
        "X ",
        "X\nY",
        "X\tY",
        'A"B',
        "A\\B",
        "A'B",
        "-x",
        ".x",
        "x" * 65,
        "A`B",
        "A#B",
        "A{B}",
        "A$B",
        "A<B>",
        None,
    ],
)
def test_invalid_names_are_rejected_without_writing(repo: Path, name: Any) -> None:
    before = snapshot(repo)
    with pytest.raises(nc.ScaffoldError):
        nc.scaffold(repo, SLUG, name, PORT)
    assert snapshot(repo) == before


@pytest.mark.parametrize("name", ["X", "ABRA Flexi", "Dotykačka", "Shop (CZ) v2.0 & co/partners+"])
def test_accepted_names_render_into_manifest_and_docstrings(name: str) -> None:
    files = nc.render(SLUG, name, PORT, nc.read_pins(ROOT))
    assert f'name: "{name}"\n' in files["connector.yaml"]
    assert name in files[f"src/{MODULE}/__init__.py"]
    assert name in files["README.md"]


def test_render_is_deterministic_complete_and_guess_free() -> None:
    pins = nc.read_pins(ROOT)
    files = nc.render(SLUG, NAME, PORT, pins)
    assert files == nc.render(SLUG, NAME, PORT, pins)
    assert set(files) == {
        "Dockerfile",
        "pyproject.toml",
        "connector.yaml",
        "README.md",
        f"src/{MODULE}/__init__.py",
        f"src/{MODULE}/__main__.py",
        f"src/{MODULE}/app.py",
        f"src/{MODULE}/schemas.py",
        f"src/{MODULE}/service.py",
        "tests/test_contract.py",
        "tests/test_service.py",
    }
    for relative, content in files.items():
        assert content.endswith("\n"), relative
        assert "\r" not in content, relative
        assert nc._PLACEHOLDER.search(content) is None, relative
        for foreign in ("raynet", "flexibee", "upgates", "dotykacka", "ares.gov"):
            assert foreign not in content.lower(), relative
    assert f"EXPOSE {PORT}\n" in files["Dockerfile"]
    assert f"COPY {SLUG} ./connector\n" in files["Dockerfile"]
    assert f'ENTRYPOINT ["python", "-m", "{MODULE}"]\n' in files["Dockerfile"]
    assert f'packages = ["{MODULE}"]\n' in files["pyproject.toml"]
    assert f"default_port={PORT})\n" in files[f"src/{MODULE}/__main__.py"]
    manifest = files["connector.yaml"]
    expected_header = f'slug: "{SLUG}"\nname: "{NAME}"\nversion: {nc.CONNECTOR_VERSION}\n'
    assert expected_header in manifest
    assert f"- name: {SLUG}_sample\n" in manifest
    # Only the SDK-mandated PII key is declared; provider keys and egress are not guessed.
    assert manifest.count("- key: ") == 1
    assert "- key: pii_key\n" in manifest
    for guessed in ("http://", "https://", "host:", "host_pattern:", "api_url", "password"):
        assert guessed not in manifest, guessed
    service = files[f"src/{MODULE}/service.py"]
    assert f'SLUG = "{SLUG}"\n' in service
    assert f'VERSION = "{nc.CONNECTOR_VERSION}"\n' in service
    assert "REQUIRED_CREDENTIALS: tuple[str, ...] = ()\n" in service
    assert "ErrorCode.INTERNAL" in service
    assert '"connected"' not in service
    for guessed in ("http://", "https://", "api_url", "api_key", "username", "password"):
        assert guessed not in service, guessed
    assert f"register {SLUG}" in files["README.md"]


def test_pins_are_read_from_sdk_and_match_reference_connector() -> None:
    pins = nc.read_pins(ROOT)
    files = nc.render(SLUG, NAME, PORT, pins)
    reference = (ROOT / REFERENCE / "Dockerfile").read_text(encoding="utf-8")
    exposed = re.search(r"^EXPOSE ([0-9]+)$", reference, re.MULTILINE)
    assert exposed is not None
    expected = (
        reference.replace(f"connector_{REFERENCE}", MODULE)
        .replace(f"COPY {REFERENCE} ", f"COPY {SLUG} ")
        .replace(exposed.group(1), str(PORT))
    )
    assert files["Dockerfile"].splitlines() == expected.splitlines()
    assert files["Dockerfile"].startswith(f"FROM {pins.base_image} AS build\n")
    reference_pyproject = (ROOT / REFERENCE / "pyproject.toml").read_text(encoding="utf-8")
    pinned = {line.strip() for line in reference_pyproject.splitlines() if "==" in line}
    assert {line.strip() for line in files["pyproject.toml"].splitlines() if "==" in line} == pinned
    assert nc.used_ports(ROOT)[int(exposed.group(1))] == REFERENCE


@pytest.mark.parametrize(
    "relative,content",
    [
        ("sdk/pyproject.toml", '[project]\nname = "x"\nversion = "0.1.0"\n'),
        ("sdk/pyproject.toml", "not = [toml\n"),
        ("scripts/Dockerfile.test", "RUN pip install PyYAML==6.0.2\n"),
        ("scripts/Dockerfile.test", "FROM python:3.13\nRUN pip install PyYAML==6.0.2\n"),
    ],
)
def test_missing_pins_fail_closed_without_writing(repo: Path, relative: str, content: str) -> None:
    (repo / relative).write_text(content, encoding="utf-8")
    before = snapshot(repo)
    with pytest.raises(nc.ScaffoldError):
        nc.scaffold(repo, SLUG, NAME, PORT)
    assert snapshot(repo) == before


def test_scaffold_publishes_complete_package_and_leaves_no_staging(repo: Path) -> None:
    before = set(os.listdir(repo))
    target = nc.scaffold(repo, SLUG, NAME, PORT)
    assert target == repo / SLUG
    assert set(os.listdir(repo)) == before | {SLUG}
    expected = nc.render(SLUG, NAME, PORT, nc.read_pins(repo))
    actual = {
        path.relative_to(target).as_posix(): path.read_bytes().decode("utf-8")
        for path in target.rglob("*")
        if path.is_file()
    }
    assert actual == expected
    published = snapshot(target)
    with pytest.raises(nc.ScaffoldError, match="existuje"):
        nc.scaffold(repo, SLUG, "Jiný název", 8107)
    assert snapshot(target) == published
    assert set(os.listdir(repo)) == before | {SLUG}


def test_scaffold_never_edits_registry_ci_or_shared_files(repo: Path) -> None:
    before = snapshot(repo)
    nc.scaffold(repo, SLUG, NAME, PORT)
    after = snapshot(repo)
    assert {key: value for key, value in after.items() if Path(key).parts[0] != SLUG} == before
    for relative, content in SHARED_FILES.items():
        assert (repo / relative).read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "kind", ["directory", "empty_directory", "file", "symlink", "dangling_symlink"]
)
def test_existing_destination_is_preserved(repo: Path, kind: str) -> None:
    target = repo / SLUG
    elsewhere = repo / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep\n", encoding="utf-8")
    if kind == "directory":
        target.mkdir()
        (target / "keep.txt").write_text("keep\n", encoding="utf-8")
    elif kind == "empty_directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("keep\n", encoding="utf-8")
    elif kind == "symlink":
        target.symlink_to(elsewhere, target_is_directory=True)
    else:
        target.symlink_to(repo / "missing")
    before = snapshot(repo)
    with pytest.raises(nc.ScaffoldError, match="existuje"):
        nc.scaffold(repo, SLUG, NAME, PORT)
    assert snapshot(repo) == before


def test_write_failure_leaves_no_partial_target(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = nc.write_file

    def failing(path: Path, content: str) -> None:
        if path.name == "service.py":
            raise OSError(28, "No space left on device")
        original(path, content)

    monkeypatch.setattr(nc, "write_file", failing)
    before = snapshot(repo)
    with pytest.raises(OSError):
        nc.scaffold(repo, SLUG, NAME, PORT)
    assert snapshot(repo) == before


def test_publish_failure_rolls_back_target(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = nc.move_entry
    moved: list[str] = []

    def failing(source: Path, destination: Path) -> None:
        moved.append(source.name)
        if len(moved) == 2:
            raise OSError(5, "Input/output error")
        original(source, destination)

    monkeypatch.setattr(nc, "move_entry", failing)
    before = snapshot(repo)
    with pytest.raises(OSError):
        nc.scaffold(repo, SLUG, NAME, PORT)
    assert len(moved) == 2
    assert snapshot(repo) == before


def test_root_must_be_an_existing_connector_repository(tmp_path: Path) -> None:
    file_root = tmp_path / "file"
    file_root.write_text("x\n", encoding="utf-8")
    for root in (tmp_path / "missing", tmp_path, file_root):
        with pytest.raises(nc.ScaffoldError):
            nc.scaffold(root, SLUG, NAME, PORT)
    assert set(os.listdir(tmp_path)) == {"file"}


@pytest.mark.parametrize("slug", ["ares", "sdk", "scripts", "tests"])
def test_real_repository_refuses_existing_directories(slug: str) -> None:
    before = set(os.listdir(ROOT))
    with pytest.raises(nc.ScaffoldError):
        nc.scaffold(ROOT, slug, NAME, PORT)
    assert set(os.listdir(ROOT)) == before


def test_cli_success_prints_register_command_and_no_secrets(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sentinel-signing-secret-value-that-must-not-leak"
    monkeypatch.setenv("OPENMCP_INTERNAL_TOKEN", sentinel)
    argv = [SLUG, "--name", NAME, "--port", str(PORT), "--root", str(repo)]
    assert nc.main(argv) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert f"python scripts/connector_inventory.py register {SLUG}" in out
    assert "nedokončený" in out
    assert sentinel not in out
    assert (repo / SLUG / "connector.yaml").is_file()
    assert (repo / "connectors.list").read_text(encoding="utf-8") == "ares\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["Bad-Slug", "--name", NAME, "--port", str(PORT)],
        ["../evil", "--name", NAME, "--port", str(PORT)],
        [SLUG, "--name", NAME, "--port", "8105"],
        [SLUG, "--name", NAME, "--port", "8150"],
        [SLUG, "--name", "", "--port", str(PORT)],
        ["legacy", "--name", NAME, "--port", str(PORT)],
    ],
)
def test_cli_rejects_invalid_input_without_creating_anything(
    repo: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    before = snapshot(repo)
    assert nc.main([*argv, "--root", str(repo)]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("chyba: ")
    assert snapshot(repo) == before


def test_generated_package_passes_lint_types_and_its_own_tests(
    repo: Path, tmp_path: Path
) -> None:
    for dependency in ("pydantic", "starlette", "httpx", "yaml"):
        __import__(dependency)
    target = nc.scaffold(repo, SLUG, NAME, PORT)
    env = os.environ | {
        "PYTHONPATH": os.pathsep.join([str(ROOT / "sdk" / "src"), str(target / "src")]),
        "MYPYPATH": str(ROOT / "sdk" / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "MYPY_CACHE_DIR": str(tmp_path / "mypy"),
        "RUFF_CACHE_DIR": str(tmp_path / "ruff"),
    }
    env.pop("OPENMCP_INTERNAL_TOKEN", None)
    env.pop("OPENMCP_INTERNAL_TOKEN_FILE", None)

    def run(*command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command, cwd=target, env=env, capture_output=True, text=True, timeout=600, check=False
        )

    tests = run(
        sys.executable, "-m", "pytest", "-q", "-W", "error", "--strict-markers",
        "-p", "no:cacheprovider", "tests",
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr
    assert " passed" in tests.stdout
    for tool, command in (
        ("ruff", ("ruff", "check", "src", "tests")),
        ("mypy", ("mypy", "--config-file", "pyproject.toml", "src")),
    ):
        assert shutil.which(tool) is not None, f"Required test tool missing: {tool}"
        result = run(*command)
        assert result.returncode == 0, result.stdout + result.stderr


def test_maximum_slug_and_display_name_still_generate_valid_python(repo: Path) -> None:
    target = nc.scaffold(repo, "a" * 32, "N" * 64, PORT)
    result = subprocess.run(
        ["ruff", "check", "--no-cache", "src", "tests"], cwd=target,
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("slug", ["no", "on", "yes", "off", "true", "null"])
def test_yaml_keyword_slug_remains_a_string(slug: str) -> None:
    import yaml

    files = nc.render(slug, NAME, PORT, nc.read_pins(ROOT))
    assert yaml.safe_load(files["connector.yaml"])["slug"] == slug
