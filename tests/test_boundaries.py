"""Architectural boundaries, enforced by test rather than by convention.

The layering in `docs/ARCHITECTURE.md` is easy to state and easy to erode: nothing
about a stray import makes a test fail, so the first person to reach across a
boundary for convenience leaves no trace. These are cheap static checks over the
import graph that fail loudly instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
SERVICE_PKGS = ("sourcing", "matching")


def _backend_sources() -> list[Path]:
    """First-party modules only — the venv holds tens of thousands of .py files."""
    skip = {"venv", "__pycache__", "site-packages", ".venv", "node_modules"}
    return sorted(p for p in BACKEND.rglob("*.py") if not skip & set(p.parts))


SRC = _backend_sources()


def _imports(path: Path) -> set[str]:
    """Dotted module names this file imports (absolute and relative, resolved)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    pkg = path.parent.relative_to(BACKEND.parent).with_suffix("").as_posix()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import -> resolve against its package
                base = pkg.split("/")
                if node.level > 1:
                    base = base[: -node.level + 1]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if target:
                found.add(target)
    return found


@pytest.mark.parametrize("path", sorted(SRC), ids=lambda p: p.name)
def test_core_never_imports_services(path: Path):
    if path.parts[-2] != "core":
        pytest.skip("not in core/")
    leaked = {m for m in _imports(path) if m.startswith("backend.services")}
    assert not leaked, f"core/{path.name} imports {sorted(leaked)}"


@pytest.mark.parametrize("path", sorted(SRC), ids=lambda p: p.name)
def test_services_never_import_the_entrypoints(path: Path):
    if "services" not in path.parts:
        pytest.skip("not in services/")
    banned = ("backend.main", "backend.graph", "backend.cli", "backend.retention")
    leaked = {m for m in _imports(path) if m.startswith(banned)}
    assert not leaked, f"services/{path.name} imports {sorted(leaked)}"


@pytest.mark.parametrize("path", sorted(SRC), ids=lambda p: p.name)
def test_service_subpackages_do_not_import_each_other(path: Path):
    """The sourcing/matching split is load-bearing, not cosmetic — see ARCHITECTURE.md."""
    if path.parent.name not in SERVICE_PKGS:
        pytest.skip("not directly in a services/ subpackage")
    mine = path.parent.name
    others = {p for p in SERVICE_PKGS if p != mine}
    leaked = {
        m for m in _imports(path)
        if any(m.startswith(f"backend.services.{o}") for o in others)
    }
    assert not leaked, (
        f"services/{mine}/{path.name} reaches into {sorted(leaked)}; "
        "the two subpackages must stay independent"
    )


def test_the_split_actually_exists():
    """Guards against a rename that quietly empties one side of the boundary."""
    for pkg in SERVICE_PKGS:
        d = BACKEND / "services" / pkg
        assert d.is_dir(), f"services/{pkg} is missing"
        modules = [p for p in d.glob("*.py") if p.name != "__init__.py"]
        assert modules, f"services/{pkg} is empty"