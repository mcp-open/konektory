"""Jediný registr konektorů: connectors.list a strukturální kontrola adaptérů.

Registr je zdroj pravdy pro seznam adaptérů. Skript ho čte a ověřuje proti
skutečnému layoutu repozitáře. Nikdy neimportuje provider moduly a neobjevuje
neregistrované adresáře jako konektory; neznámý adresář s `connector.yaml` je
chyba, ne tichá registrace.

Použití:
    python scripts/connector_inventory.py [--root DIR] list
    python scripts/connector_inventory.py [--root DIR] check
    python scripts/connector_inventory.py [--root DIR] register <slug>
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_NAME = "connectors.list"
# Konzervativní slug: začíná písmenem, jen [a-z0-9], délka 2–32. Nedovolí tečku,
# lomítko ani prázdný řetězec, takže z něj nejde složit cesta mimo repozitář.
SLUG_PATTERN = re.compile(r"[a-z][a-z0-9]{1,31}")
REQUIRED_FILES = ("connector.yaml", "Dockerfile", "pyproject.toml")


class InventoryError(Exception):
    """Registr nebo layout konektoru není v použitelném stavu."""


def registry_path(root: Path) -> Path:
    return root / REGISTRY_NAME


def load_connectors(root: Path) -> list[str]:
    """Vrátí slugy v pořadí registru; jinak vyhodí InventoryError.

    Formát je záměrně bez komentářů a prázdných řádků: jeden slug na řádek,
    ukončený `\\n`. Duplicita ani nepovolený znak se nepřeskakuje, ale odmítá.
    """
    path = registry_path(root)
    if path.is_symlink():
        raise InventoryError(f"{REGISTRY_NAME}: registr je symlink, odmítnuto")
    try:
        if not path.is_file():
            raise InventoryError(f"{REGISTRY_NAME}: registr není běžný soubor")
        raw = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise InventoryError(f"{REGISTRY_NAME}: registr chybí") from None
    except (OSError, UnicodeDecodeError) as error:
        raise InventoryError(
            f"{REGISTRY_NAME}: registr nelze přečíst ({error.__class__.__name__})"
        ) from error
    # Dělíme jen na "\n"; str.splitlines() by rozdělil i na \r, \x0b nebo U+2028.
    lines = raw.split("\n")
    if lines[-1] != "":
        raise InventoryError(f"{REGISTRY_NAME}: poslední řádek musí končit koncem řádku")
    connectors: list[str] = []
    for number, line in enumerate(lines[:-1], start=1):
        if not SLUG_PATTERN.fullmatch(line):
            raise InventoryError(f"{REGISTRY_NAME}:{number}: nepovolený záznam {line[:40]!r}")
        if line in connectors:
            raise InventoryError(f"{REGISTRY_NAME}:{number}: duplicitní slug {line!r}")
        connectors.append(line)
    if not connectors:
        raise InventoryError(f"{REGISTRY_NAME}: registr je prázdný")
    return connectors


def validate_connector(root: Path, slug: str) -> list[str]:
    """Vrátí seznam strukturálních problémů konektoru; prázdný seznam znamená OK.

    Kontroluje jen existenci a typ souborů — nic z konektoru se neimportuje ani
    nespouští. Symlink kdekoli v cestě je problém, ne platný soubor.
    """
    if not SLUG_PATTERN.fullmatch(slug):
        return [f"{slug[:40]!r}: nepovolený slug"]
    directory = root / slug
    if directory.is_symlink() or not directory.is_dir():
        return [f"{slug}/: chybí adresář konektoru nebo není běžný adresář"]
    problems: list[str] = []
    for relative in (*REQUIRED_FILES, f"src/connector_{slug}/app.py"):
        target = _path_without_links(directory, relative)
        if target is None or not target.is_file():
            problems.append(f"{slug}/{relative}: chybí běžný soubor (nebo je symlink)")
    tests = _path_without_links(directory, "tests")
    if tests is None or not tests.is_dir():
        problems.append(f"{slug}/tests/: chybí adresář testů (nebo je symlink)")
    return problems


def check(root: Path) -> list[str]:
    """Vrátí všechny nalezené problémy registru a layoutu konektorů."""
    try:
        connectors = load_connectors(root)
    except InventoryError as error:
        return [str(error)]
    problems: list[str] = []
    for slug in connectors:
        problems.extend(validate_connector(root, slug))
    problems.extend(_unregistered_directories(root, connectors))
    return problems


def register(root: Path, slug: str) -> None:
    """Zaregistruje už naskafoldovaný konektor do registru.

    Preflight nejdřív ověří, že stávající registr je konzistentní a že nový
    konektor má kompletní strukturu. Zápis je jedna atomická výměna
    (`os.replace`); původní soubor se nikdy nezkracuje ani nemaže.
    """
    if not SLUG_PATTERN.fullmatch(slug):
        raise InventoryError(f"{slug[:40]!r}: nepovolený slug, povoleno [a-z][a-z0-9]{{1,31}}")
    connectors = load_connectors(root)
    if slug in connectors:
        raise InventoryError(f"{slug}: už je v {REGISTRY_NAME}")
    problems = [problem for existing in [*connectors, slug]
                for problem in validate_connector(root, existing)]
    if problems:
        raise InventoryError(f"{slug}: neúplný konektor, neregistrováno: " + "; ".join(problems))
    updated = [*connectors, slug]
    _atomic_write(registry_path(root), "".join(f"{name}\n" for name in updated))


def _path_without_links(base: Path, relative: str) -> Path | None:
    """Poskládá cestu pod `base` a odmítne ji, pokud je kterákoli komponenta symlink."""
    current = base
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return None
    return current


def _unregistered_directories(root: Path, connectors: Sequence[str]) -> list[str]:
    registered = set(connectors)
    try:
        entries = sorted(root.iterdir())
    except OSError as error:
        return [f"{root}: adresář nelze přečíst ({error.__class__.__name__})"]
    problems = []
    for entry in entries:
        if entry.name in registered or not entry.is_dir():
            continue
        if (entry / "connector.yaml").exists():
            problems.append(
                f"{entry.name}/connector.yaml: vypadá jako konektor, ale chybí v {REGISTRY_NAME}"
            )
    return problems


def _atomic_write(path: Path, text: str) -> None:
    """Zapíše soubor přes dočasnou kopii a `os.replace`; cíl se nikdy nezkracuje."""
    if path.parent.is_symlink() or path.is_symlink():
        raise InventoryError(f"{path.name}: cíl zápisu je symlink, odmítnuto")
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 — zavíráme přes with níž
        "w", encoding="utf-8", dir=directory, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(directory)


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # Např. DrvFs/Windows; výměna samotná je i tak atomická.
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="kořen repozitáře (výchozí: podle skriptu)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="vypíše registrované slugy v pořadí registru")
    commands.add_parser("check", help="ověří registr a layout konektorů")
    registration = commands.add_parser("register", help="zaregistruje naskafoldovaný konektor")
    registration.add_argument("slug")
    args = parser.parse_args(argv)
    root = args.root
    try:
        if args.command == "list":
            for slug in load_connectors(root):
                print(slug)
            return 0
        if args.command == "check":
            problems = check(root)
            for problem in problems:
                print(problem, file=sys.stderr)
            if problems:
                return 1
            print(f"Registr OK: {len(load_connectors(root))} konektorů")
            return 0
        register(root, args.slug)
        print(f"Registrováno: {args.slug} ({REGISTRY_NAME})")
        return 0
    except InventoryError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
