"""Dependency pinning: the runtime imports and requirements.txt must not drift.

The deploy runs `pip install -r requirements.txt` into a function bundle that is
imported by `app.py` at cold start. A `ModuleNotFoundError` there blocks every
route, `/` included. So the test suite must fail at test time, not at cold
start, on one of three classes of mismatch: a third-party package imported but
not pinned, a top-level third-party import missing from the list, or sklearn
(in requirements-dev only) leaking into the request import path.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

# Subtrees excluded from the *runtime* parity check because their third-party
# imports live in requirements-dev.txt instead:
#   experiments/    the abandoned surge regression (kept for the record only)
#   tools/          one-off render/export scripts, run by a human, never served
#   data_pipeline/  pre-fetch scripts that produce data/ at ingestion time
EXCLUDED = ("experiments", "tools", "data_pipeline", "__pycache__")

STDLIB = sys.stdlib_module_names

# The import root of a third-party package is not always the PyPI package name.
IMPORT_ROOT_TO_PACKAGE = {
    "google": "google-genai",
    "dotenv": "python-dotenv",
}

# Modules that are part of the repo, so they are never third-party.
def _first_party_roots() -> set[str]:
    roots: set[str] = set()
    # backend/ own dir (the 'backend' package root) …
    roots.add("backend")
    # … any top-level *.py directly under backend/ (e.g. main, locations) and
    # any sub-package under backend/ (e.g. ml, simulation, cyclones, weather, ai).
    for entry in BACKEND.iterdir():
        if entry.is_dir():
            roots.add(entry.name)
        elif entry.is_file() and entry.suffix == ".py":
            roots.add(entry.stem)
    # Anything importable from the repo root.
    for entry in REPO_ROOT.iterdir():
        if entry.is_dir() and not entry.name.startswith("."):
            roots.add(entry.name)
        elif entry.is_file() and (entry.suffix == ".py"):
            roots.add(entry.stem)
    return roots


def _is_stdlib(root: str) -> bool:
    return root in STDLIB or root == "__future__"


def _requirements_names() -> set[str]:
    names: set[str] = set()
    for raw in (REPO_ROOT / "requirements.txt").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line
        for marker in ("[", ">=", "<=", "==", "~=", "!=", ">", "<", ";"):
            idx = name.find(marker)
            if idx != -1:
                name = name[:idx]
        name = name.strip().lower().replace("_", "-")
        if name:
            names.add(name)
    return names


def _runtime_third_party_roots(path: Path) -> set[str]:
    """Every third-party import that actually runs when `path` is imported.

    Only module-level imports count. Imports **inside a function body**, **under
    a `class` body**, or **under an `if TYPE_CHECKING:` guard** do not — even
    though AST-walk would find them. That exclusion is the whole design: the
    trainer/fite-evaluate code imports scikit-learn lazily inside
    `storm_peak_intensity`'s function bodies, so it never executes at request
    time, and the parity check must not count it.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()

    def _sweep(stmts):
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.If):
                if not (isinstance(stmt.test, ast.Name) and stmt.test.id == "TYPE_CHECKING"):
                    _sweep(stmt.body)
                    _sweep(stmt.orelse)
                continue
            if isinstance(stmt, ast.Try):
                _sweep(stmt.body)
                for handler in stmt.handlers:
                    _sweep(handler.body)
                continue
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    roots.add(alias.name.split(".")[0])
                continue
            if isinstance(stmt, ast.ImportFrom):
                if stmt.level == 0 and stmt.module:
                    roots.add(stmt.module.split(".")[0])
                continue
            _sweep(list(ast.iter_child_nodes(stmt)))

    _sweep(tree.body)
    return roots


def test_every_runtime_third_party_import_is_a_pinned_requirement() -> None:
    import sys

    first_party = _first_party_roots()
    third_party: dict[str, set[str]] = {}

    candidate_files = []
    for path in BACKEND.rglob("*.py"):
        s = str(path)
        if any(piece in s for piece in EXCLUDED):
            continue
        candidate_files.append(path)
    candidate_files.append(REPO_ROOT / "app.py")

    for path in candidate_files:
        for root in _runtime_third_party_roots(path):
            if _is_stdlib(root) or root in first_party:
                continue
            third_party.setdefault(root, set()).add(str(path.relative_to(REPO_ROOT)))

    requirements = _requirements_names()

    missing: dict[str, set[str]] = {}
    for root, sites in sorted(third_party.items()):
        package = IMPORT_ROOT_TO_PACKAGE.get(root, root).lower().replace("_", "-")
        if package not in requirements:
            missing[package] = sites

    assert not missing, (
        "Runtime code imports packages that are not in requirements.txt. The Vercel "
        "function bundle is built from requirements.txt, so these would be "
        "ModuleNotFoundError there. Add the package to requirements.txt if it belongs "
        "at request time; move its import behind a function scope / lazy boundary if "
        "it does not; and keep the Mapping in IMPORT_ROOT_TO_PACKAGE up to date.\n"
        + "\n".join(f"  {pkg} imported from: {sorted(sites)}" for pkg, sites in missing.items())
    )


def test_requirements_count_includes_everything_the_treepath_needs() -> None:
    """Guard the explicit is-sklearn-optional line: requirements.txt has no
    third-party import that this tree can't actually name, and vice-versa — a
    pin must correspond to at least one runtime import root. This is what would
    have caught the silent sklearn gap at review time: n this tree no line in
    requirements.txt is dead."""
    import sys

    first_party = _first_party_roots()
    runtime_roots: set[str] = set()
    for path in BACKEND.rglob("*.py"):
        s = str(path)
        if any(piece in s for piece in EXCLUDED):
            continue
        for root in _runtime_third_party_roots(path):
            if _is_stdlib(root) or root in first_party:
                continue
            runtime_roots.add(root)
    for root in _runtime_third_party_roots(REPO_ROOT / "app.py"):
        if _is_stdlib(root) or root in first_party:
            continue
        runtime_roots.add(root)

    requirements = _requirements_names()
    consumed = {
        IMPORT_ROOT_TO_PACKAGE.get(root, root).lower().replace("_", "-")
        for root in runtime_roots
    }
    dead = sorted(set(requirements) - consumed)
    assert not dead, (
        "requirements.txt pins packages that nothing imports at request time. Either "
        f"the service lost the last importer (remove the pin) or a new module was added "
        f"that quietly now needs it listed. Dead pins: {dead}"
    )


def test_baseline_estimate_works_when_sklearn_is_unavailable() -> None:
    """/risk-analyst's figure must not need scikit-learn at request time.

    In the deployed bundle there is no scikit-learn, so importing backend.main
    and asking for the shipped estimate must work with the sklearn import
    machinery hard-blocked. Asserts not only that the call succeeds and returns
    the same constant, but that importing backend.main never brought sklearn in
    either — the explicit point of the offline/runtime split.
    """
    saved: dict[str, object] = {}
    for key in list(sys.modules):
        if key == "sklearn" or key.startswith("sklearn."):
            saved[key] = sys.modules.pop(key)
    sys.modules["sklearn"] = None  # type: ignore[assignment]
    try:
        # Importing at all is the fragile part; it must succeed here.
        import backend.main  # noqa: F401

        from backend.ml.storm_peak_intensity import baseline_estimate

        estimate = baseline_estimate()
        assert estimate.estimate_kt == 50.0
        assert estimate.estimate_source == "median_baseline"
        assert estimate.is_a_prediction is False
        assert estimate.beats_baseline is False
        assert estimate.n_training == 300
        assert estimate.baseline_kt == 50.0
    finally:
        # Importing the app must not pull in any piece of sklearn: with the root
        # blocked by None, no real sklearn object should have been importable, so
        # every sys.modules key beginning 'sklearn.' must still be absent.
        leaked = [k for k in sys.modules if k.startswith("sklearn.")]
        assert not leaked, (
            f"importing backend.main leaked sklearn into sys.modules: {leaked}"
        )
        # And the root sentinel must not have been quietly replaced by the real
        # package — that would mean some code re-imported it around the block.
        assert sys.modules.get("sklearn") is None
        sys.modules.pop("sklearn", None)
        # Restore the real sklearn entries we popped so other tests see the full vendor.
        sys.modules.update(saved)


def test_importing_backend_main_does_not_import_sklearn() -> None:
    """Companion to the baseline-estimate test: the import graph itself is
    sklearn-free. Guards against a stray shaped sklearn import creeping into any
    runtime module's top level, which is what the ModuleNotFoundError was."""
    saved: dict[str, object] = {}
    for key in list(sys.modules):
        if key == "sklearn" or key.startswith("sklearn."):
            saved[key] = sys.modules.pop(key)
    sys.modules["sklearn"] = None  # type: ignore[assignment]
    try:
        import backend.main  # noqa: F401

        leaked = [k for k in sys.modules if k == "sklearn" or k.startswith("sklearn.")]
        assert leaked == ["sklearn"], f"expected only the None-sentinel for 'sklearn', got {leaked}"
    finally:
        sys.modules.pop("sklearn", None)
        sys.modules.update(saved)
