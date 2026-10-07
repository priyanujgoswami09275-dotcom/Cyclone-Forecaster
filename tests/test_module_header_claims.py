"""The module docstring makes claims a reader will rely on. They must be true.

Three were wrong, each in a way that reads as confident prose:

- *"results are cached by category"* — they are cached on
  `(cyclone_id, scenario_id)` via `ScenarioContext`. Both name a strength and
  only one can win, but a category-keyed cache hands the second storm the
  first one's flood extent, which is the exact bug this repo documents at
  length (MEMORY.md §31 and the cache-isolation tests).
- *"The only endpoint in this service that reaches the network"* — in
  `/advisory`'s docstring. `/risk-analyst` calls the same model through the
  same retry ladder, and `/live-cyclone` probes four ATCF endpoints. Three do.
- *"~6 s of raster work"* at the top band — measured 16.7 s cold.

Prose is not usually worth a test. This one is, because these specific claims
have each been read as fact by a later change and acted on.
"""

from __future__ import annotations

import ast
from pathlib import Path

MAIN_PY = Path(__file__).resolve().parents[1] / "backend" / "main.py"


def _module_docstring() -> str:
    return ast.get_docstring(ast.parse(MAIN_PY.read_text())) or ""


def _handler_docstring(name: str) -> str:
    tree = ast.parse(MAIN_PY.read_text())
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
    )
    return ast.get_docstring(handler) or ""


def test_the_cache_key_is_named_accurately() -> None:
    header = _module_docstring()
    assert "cached by category" not in header, (
        "the caches are keyed on (cyclone_id, scenario_id), not the category — a "
        "category-keyed cache serves one storm's flood extent for another"
    )
    assert "cyclone_id, scenario_id" in header, (
        "the header must name the real key, or it is just vaguer than before"
    )


def test_the_network_claim_counts_all_three_routes() -> None:
    advisory = _handler_docstring("advisory")
    assert "only endpoint" not in advisory, (
        "/risk-analyst and /live-cyclone also reach the network"
    )
    for other in ("/risk-analyst", "/live-cyclone"):
        assert other in advisory, f"{other} reaches the network too; name it"


def test_advisory_is_still_the_only_model_call_on_a_button_press() -> None:
    """The distinction that survives the correction, and is the real rule.

    Narrowing "reaches the network" must not lose what the original claim was
    for: only a human pressing Generate Advisory may spend a model call.
    """
    advisory = _handler_docstring("advisory")
    assert "button" in advisory
    assert "only one that calls a *model*" in advisory


def test_the_flood_figure_is_not_the_measured_one() -> None:
    header = _module_docstring()
    assert "~6 s" not in header, (
        "the top-band flood measures 16.7 s cold on this machine; a 6 s figure "
        "understates the cost the cache exists to avoid"
    )
