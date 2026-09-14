"""Scaffold a new read-only connector package from the pinned SDK conventions.

Standard library only. The generated package follows the existing
``build_definition`` / ``create_app`` / ``run`` pattern, reuses the pinned base
image and dependency versions read from ``sdk/`` and ``scripts/Dockerfile.test``,
and is deliberately unfinished: its sample tool and safe-test raise a safe SDK
error until a real provider is wired in. Nothing is registered or activated;
``connectors.list``, Makefile and CI stay untouched.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates" / "connector"
SLUG_PATTERN = re.compile(r"[a-z][a-z0-9]{1,31}")
PORT_RANGE = range(8106, 9000)
NAME_MAX_LENGTH = 64
NAME_EXTRA_CHARACTERS = frozenset(" .&+/()-")
RESERVED_SLUGS = frozenset(
    {
        "build",
        "connector",
        "core",
        "dist",
        "docs",
        "old",
        "openmcp",
        "platform",
        "runtime",
        "scripts",
        "sdk",
        "src",
        "test",
        "tests",
    }
)
CONNECTOR_VERSION = "0.1.0"
TOOL_DESCRIPTION = "Ukázkový nástroj scaffoldu; bez napojeného providera vrací bezpečnou chybu."
REGISTER_COMMAND = "python scripts/connector_inventory.py register {slug}"
_PLACEHOLDER = re.compile(r"__([A-Z][A-Z0-9_]*)__")
_PIN_VALUE = re.compile(r"[0-9][0-9A-Za-z.+-]*")
_IMAGE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*")
LAYOUT: tuple[tuple[str, str], ...] = (
    ("Dockerfile", "image.tmpl"),
    ("pyproject.toml", "pyproject.toml.tmpl"),
    ("connector.yaml", "connector.yaml.tmpl"),
    ("README.md", "README.md.tmpl"),
    ("src/{module}/__init__.py", "src/__init__.py.tmpl"),
    ("src/{module}/__main__.py", "src/__main__.py.tmpl"),
    ("src/{module}/app.py", "src/app.py.tmpl"),
    ("src/{module}/schemas.py", "src/schemas.py.tmpl"),
    ("src/{module}/service.py", "src/service.py.tmpl"),
    ("tests/test_contract.py", "tests/test_contract.py.tmpl"),
    ("tests/test_service.py", "tests/test_service.py.tmpl"),
)


class ScaffoldError(ValueError):
    """Input or repository state that must stop the scaffold before any write."""


@dataclass(frozen=True)
class Pins:
    """Versions copied from the repository so a new package cannot drift from the SDK."""

    base_image: str
    setuptools_version: str
    sdk_version: str
    pytest_version: str
    pyyaml_version: str


def validate_slug(slug: object) -> str:
    if not isinstance(slug, str) or SLUG_PATTERN.fullmatch(slug) is None:
        raise ScaffoldError(
            "Slug musí odpovídat [a-z][a-z0-9]{1,31}: malá písmena a číslice, bez pomlček, "
            "podtržítek, lomítek a teček."
        )
    if slug in RESERVED_SLUGS:
        raise ScaffoldError(f"Slug '{slug}' je rezervovaný název adresáře.")
    return slug


def validate_name(name: object) -> str:
    valid = (
        isinstance(name, str)
        and 1 <= len(name) <= NAME_MAX_LENGTH
        and name == name.strip()
        and name.isprintable()
        and name[0].isalnum()
        and all(char.isalnum() or char in NAME_EXTRA_CHARACTERS for char in name)
    )
    if not valid:
        raise ScaffoldError(
            "Zobrazovaný název má 1-64 tisknutelných znaků, začíná písmenem nebo číslicí a "
            "kromě písmen, číslic a mezer smí obsahovat jen . & + / ( ) -"
        )
    assert isinstance(name, str)
    return name


def validate_port(port: object) -> int:
    if isinstance(port, str) and re.fullmatch(r"[0-9]{1,5}", port):
        port = int(port)
    if isinstance(port, bool) or not isinstance(port, int) or port not in PORT_RANGE:
        raise ScaffoldError(
            f"Port musí být celé číslo {PORT_RANGE.start}..{PORT_RANGE.stop - 1}; "
            "8101-8108 patří existujícím konektorům."
        )
    return port


def validate_root(root: object) -> Path:
    path = Path(root) if isinstance(root, str | os.PathLike) else None
    if path is None or not path.is_dir():
        raise ScaffoldError("Kořen repozitáře musí být existující adresář.")
    if not (path / "sdk" / "pyproject.toml").is_file():
        raise ScaffoldError("Kořen repozitáře musí obsahovat sdk/pyproject.toml.")
    return path


def used_ports(root: Path) -> dict[int, str]:
    """Ports already exposed by sibling connector Dockerfiles, keyed to their directory."""

    found: dict[int, str] = {}
    for child in sorted(root.iterdir()):
        dockerfile = child / "Dockerfile"
        if child.name.startswith(".") or not child.is_dir() or not dockerfile.is_file():
            continue
        text = dockerfile.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"^EXPOSE\s+([0-9]{1,5})\s*$", text, re.MULTILINE):
            found.setdefault(int(match.group(1)), child.name)
    return found


def _pinned(requirements: object, package: str) -> str:
    if isinstance(requirements, list):
        for requirement in requirements:
            if isinstance(requirement, str) and requirement.startswith(f"{package}=="):
                version = requirement.removeprefix(f"{package}==")
                if _PIN_VALUE.fullmatch(version):
                    return version
    raise ScaffoldError(f"V sdk/pyproject.toml chybí připnutá verze {package}==.")


def read_pins(root: Path) -> Pins:
    """Read every reused pin from the repository; a missing pin stops the scaffold."""

    try:
        pyproject = tomllib.loads((root / "sdk" / "pyproject.toml").read_text(encoding="utf-8"))
        dockerfile = (root / "scripts" / "Dockerfile.test").read_text(encoding="utf-8")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ScaffoldError(
            "Nelze načíst sdk/pyproject.toml nebo scripts/Dockerfile.test s připnutými verzemi."
        ) from exc
    project = pyproject.get("project", {})
    sdk_version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(sdk_version, str) or not _PIN_VALUE.fullmatch(sdk_version):
        raise ScaffoldError("V sdk/pyproject.toml chybí project.version.")
    extras = project.get("optional-dependencies")
    test_extras = extras.get("test") if isinstance(extras, dict) else None
    build_system = pyproject.get("build-system")
    build_requires = build_system.get("requires") if isinstance(build_system, dict) else None
    from_lines = [line.split() for line in dockerfile.splitlines() if line.startswith("FROM ")]
    base_image = from_lines[0][1] if from_lines and len(from_lines[0]) > 1 else ""
    if not _IMAGE_REFERENCE.fullmatch(base_image) or re.search(
        r"@sha256:[a-f0-9]{64}$", base_image
    ) is None:
        raise ScaffoldError("V scripts/Dockerfile.test chybí připnutý základní image.")
    pyyaml = re.search(r"PyYAML==([0-9][0-9A-Za-z.+-]*)", dockerfile)
    if pyyaml is None:
        raise ScaffoldError("V scripts/Dockerfile.test chybí připnutá verze PyYAML==.")
    return Pins(
        base_image=base_image,
        setuptools_version=_pinned(build_requires, "setuptools"),
        sdk_version=sdk_version,
        pytest_version=_pinned(test_extras, "pytest"),
        pyyaml_version=pyyaml.group(1),
    )


def class_name(slug: str) -> str:
    return f"{slug[0].upper()}{slug[1:]}Service"


def render(slug: str, name: str, port: int, pins: Pins) -> dict[str, str]:
    """Deterministically render every file of the package as ``relative path -> content``."""

    slug = validate_slug(slug)
    name = validate_name(name)
    port = validate_port(port)
    module = f"connector_{slug}"
    values = {
        "SLUG": slug,
        "NAME": name,
        "PORT": str(port),
        "MODULE": module,
        "CLASS": class_name(slug),
        "TOOL": f"{slug}_sample",
        "TOOL_DESCRIPTION": TOOL_DESCRIPTION,
        "VERSION": CONNECTOR_VERSION,
        "BASE_IMAGE": pins.base_image,
        "SETUPTOOLS_VERSION": pins.setuptools_version,
        "SDK_VERSION": pins.sdk_version,
        "PYTEST_VERSION": pins.pytest_version,
        "PYYAML_VERSION": pins.pyyaml_version,
    }

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ScaffoldError(f"Šablona obsahuje neznámý placeholder __{key}__.")
        return values[key]

    rendered: dict[str, str] = {}
    for relative, template_name in LAYOUT:
        template_path = TEMPLATES / template_name
        try:
            template = template_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ScaffoldError(f"Chybí nebo je poškozená šablona {template_name}.") from exc
        content = _PLACEHOLDER.sub(substitute, template)
        if not content.endswith("\n"):
            content += "\n"
        rendered[relative.format(module=module)] = content
    return rendered


def write_file(path: Path, content: str) -> None:
    """Exclusive create with LF line endings; never overwrites."""

    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def move_entry(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def scaffold(root: str | os.PathLike[str], slug: str, name: str, port: int | str) -> Path:
    """Validate, render in memory, stage, then fill an exclusively created target.

    The staging directory lives next to the target so publication is a handful of
    same-filesystem renames into an exclusively created directory. Any failure
    removes the staging area and target after an ordinary write error. SIGKILL
    or power loss may leave a partial target; it is never registered and a later
    invocation refuses to overwrite it. Publication is not one atomic rename.
    """

    root_path = validate_root(root)
    slug = validate_slug(slug)
    name = validate_name(name)
    port = validate_port(port)
    target = root_path / slug
    if target.is_symlink() or target.exists():
        raise ScaffoldError(f"Cíl {slug}/ už existuje; existující obsah se nepřepisuje.")
    owners = used_ports(root_path)
    if port in owners:
        raise ScaffoldError(f"Port {port} už používá konektor {owners[port]}.")
    files = render(slug, name, port, read_pins(root_path))

    try:
        staging = Path(tempfile.mkdtemp(prefix=f".new-connector-{slug}-", dir=root_path))
    except OSError as exc:
        raise ScaffoldError(f"Do kořene repozitáře nelze zapisovat: {exc.strerror}") from exc
    try:
        for relative, content in files.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            write_file(path, content)
        try:
            os.mkdir(target)
        except FileExistsError as exc:
            raise ScaffoldError(f"Cíl {slug}/ mezitím vznikl; nic nebylo přepsáno.") from exc
        try:
            for child in sorted(staging.iterdir()):
                move_entry(child, target / child.name)
        except OSError:
            shutil.rmtree(target, ignore_errors=True)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="new_connector.py",
        description="Vytvoří nedokončený scaffold read-only konektoru nad sdíleným SDK.",
    )
    parser.add_argument("slug", help="adresář i modul connector_<slug>; [a-z][a-z0-9]{1,31}")
    parser.add_argument("--name", required=True, help="zobrazovaný název providera")
    parser.add_argument(
        "--port", required=True, help=f"vývojový port {PORT_RANGE.start}..{PORT_RANGE.stop - 1}"
    )
    parser.add_argument(
        "--root", default=DEFAULT_ROOT, type=Path, help="kořen repozitáře konektorů"
    )
    args = parser.parse_args(argv)
    try:
        target = scaffold(args.root, args.slug, args.name, args.port)
    except ScaffoldError as exc:
        print(f"chyba: {exc}", file=sys.stderr)
        return 2
    print(
        f"Vytvořen scaffold konektoru '{args.slug}' ({args.name}) v {target} "
        f"s vývojovým portem {args.port}.\n"
        "Balíček je záměrně nedokončený: ukázkový nástroj i safe-test vrací bezpečnou chybu "
        "SDK,\nprovider endpoint ani credentials nejsou odhadnuté a nic není aktivované.\n"
        "Registr connectors.list, Makefile ani CI nebyly změněny.\n\n"
        "Další krok:\n"
        f"  {REGISTER_COMMAND.format(slug=args.slug)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
