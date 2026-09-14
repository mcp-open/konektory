"""Registr konektorů drží layout adaptérů i registraci nového adaptéru v jednom stavu.

Testy nikdy nezapisují do repozitáře; každý scénář si staví vlastní kořen v tmp_path.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_INITIAL = ["ares", "dotykacka", "abraflexi", "raynet", "upgates"]


def _load_inventory():
    # Skript se načítá podle cesty; testy tak nezávisí na sys.path ani na balíčku scripts/.
    path = ROOT / "scripts" / "connector_inventory.py"
    spec = importlib.util.spec_from_file_location("connector_inventory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory = _load_inventory()


def scaffold(root: Path, slug: str) -> Path:
    """Minimální layout, který `validate_connector` považuje za kompletní."""
    directory = root / slug
    (directory / "src" / f"connector_{slug}").mkdir(parents=True)
    (directory / "tests").mkdir()
    for name in ("connector.yaml", "Dockerfile", "pyproject.toml"):
        (directory / name).write_text(f"# {slug}\n", encoding="utf-8")
    (directory / "src" / f"connector_{slug}" / "app.py").write_text("", encoding="utf-8")
    return directory


def build_root(tmp_path: Path, slugs: Sequence[str] = ("alpha", "beta")) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for slug in slugs:
        scaffold(root, slug)
    (root / "sdk").mkdir()  # sdílený balík není konektor a nesmí se do registru nabízet
    (root / "connectors.list").write_text(
        "".join(f"{slug}\n" for slug in slugs), encoding="utf-8"
    )
    return root


def snapshot(root: Path) -> bytes:
    return (root / "connectors.list").read_bytes()


def symlink(target: Path, link_path: Path) -> None:
    try:
        link_path.symlink_to(target)
    except OSError as error:  # pragma: no cover - filesystém bez podpory symlinků
        pytest.skip(f"symlinky nejsou v tomto prostředí k dispozici: {error}")


# --- Skutečný repozitář ------------------------------------------------------


def test_repository_registry_starts_with_the_five_ported_providers() -> None:
    assert inventory.load_connectors(ROOT)[: len(EXPECTED_INITIAL)] == EXPECTED_INITIAL


# --- Generovaný include ------------------------------------------------------


# --- Registr -----------------------------------------------------------------


# --- CLI ---------------------------------------------------------------------


def test_cli_list_prints_registry_in_order(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = build_root(tmp_path, ("beta", "alpha"))
    assert inventory.main(["--root", str(root), "list"]) == 0
    assert capsys.readouterr().out == "beta\nalpha\n"


def test_cli_check_exit_codes(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    assert inventory.main(["--root", str(root), "check"]) == 0
    scaffold(root, "gama")
    assert inventory.main(["--root", str(root), "check"]) == 1
    (root / "connectors.list").write_text("alpha\nalpha\n", encoding="utf-8")
    assert inventory.main(["--root", str(root), "check"]) == 1


def test_cli_register_reports_unknown_connector(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    assert inventory.main(["--root", str(root), "register", "gama"]) == 2
    assert inventory.load_connectors(root) == ["alpha", "beta"]


