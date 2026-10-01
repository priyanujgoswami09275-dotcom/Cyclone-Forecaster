"""Cache isolation: a result computed for one storm must never answer for another.

## Why these tests are the important ones in this feature

Every other failure mode in this codebase raises. A wrong cache key does not: it
returns a plausible number, a plausible polygon, a map that draws confidently.
Two cyclones at the same strength resolve to the same wind, so a cache keyed on
the wind or the category cannot tell them apart — and the second one silently
receives the first one's flood extent over the delta.

So the assertions below are mostly of the shape "these two must **not** be the
same object", which is the opposite of what a cache test usually checks.

`test_no_lru_cache_in_main_is_keyed_on_a_bare_category` is a guard over the
source text rather than over behaviour. It is brittle on purpose: the bug class
is *a function whose key omits the storm*, and only a source-level check catches
a function that nobody calls yet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.cyclones.scenarios import DEFAULT_CYCLONE_ID, ScenarioContext
from backend.main import (
    allocation_for_scenario,
    flood_for_scenario,
    surge_for_scenario,
)

MAIN_PY = Path(__file__).resolve().parents[1] / "backend" / "main.py"

# Two real IBTrACS SIDs from different storms in different decades.
REMAL = "2024145N14087"
OTHER = "1970324N05143"


# --- the caches themselves --------------------------------------------------


def test_two_cyclones_at_the_same_scenario_do_not_share_an_entry() -> None:
    """The core regression. Same scenario, different storm, different result."""
    a = flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    b = flood_for_scenario(ScenarioContext(OTHER, "cat6"))
    assert a is not b, "one storm's flood extent was served for another"


def test_the_same_context_hits_the_cache() -> None:
    """A key that never hits is not a cache; this is the other half of the test."""
    flood_for_scenario.cache_clear()
    first = flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    second = flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    assert first is second


def test_the_cache_info_grows_with_distinct_contexts() -> None:
    flood_for_scenario.cache_clear()
    for cyclone in (REMAL, OTHER, "1970326N10071", "1971124N10093"):
        flood_for_scenario(ScenarioContext(cyclone, "cat5"))
    assert flood_for_scenario.cache_info().currsize == 4


def test_observed_and_the_band_it_matches_are_different_entries() -> None:
    """Even when two scenarios resolve to the same wind.

    A context keyed on the resolved wind would hand `observed` back whatever
    band sits at that value, which is the same bug with a narrower blast radius.
    """
    a = ScenarioContext(REMAL, "observed")
    b = ScenarioContext(REMAL, "cat3")
    assert a.key != b.key
    assert a != b
    assert hash(a) != hash(b)


def test_the_surge_cache_is_isolated_too() -> None:
    surge_for_scenario.cache_clear()
    a = surge_for_scenario(ScenarioContext(REMAL, "cat6"))
    b = surge_for_scenario(ScenarioContext(OTHER, "cat6"))
    # Same wind, so the same number: the deterministic law takes only a wind.
    # What must differ is the *entry*, not necessarily the value.
    assert a == b, "the law itself takes one input; a difference here is a bug"
    assert surge_for_scenario.cache_info().currsize == 2


def test_allocation_is_isolated() -> None:
    a = allocation_for_scenario(ScenarioContext(REMAL, "cat6"))
    b = allocation_for_scenario(ScenarioContext(OTHER, "cat6"))
    assert a is not b


# --- the source-level guard -------------------------------------------------

_CACHE_FN = re.compile(r"@lru_cache\([^)]*\)\s*\ndef\s+(\w+)\(([^)]*)\)", re.S)

#: Caches that legitimately take no context, each with the reason. Growing this
#: list needs a reason in the entry, which is the point.
CONTEXT_FREE_CACHES = {
    "_component_index": "the road graph, which does not depend on any storm",
    "_catalogue": "lives in registry.py, keyed on nothing because it is the list",
    "_registry_singleton": "a singleton constructor",
    "_raw_rows": "a test helper over the archive file",
}


def test_no_lru_cache_in_main_is_keyed_on_a_bare_category() -> None:
    """The bug class, caught before anyone calls the function.

    Only a source check does this. A behavioural test proves nothing about a
    cache nobody exercises, and by the time a caller hits it the wrong entry has
    already been served.
    """
    source = MAIN_PY.read_text()
    offenders = []
    for match in _CACHE_FN.finditer(source):
        name, params = match.group(1), match.group(2)
        if name in CONTEXT_FREE_CACHES:
            continue
        arguments = [
            part.split(":")[0].split("=")[0].strip()
            for part in params.split(",")
            if part.strip()
        ]
        if "ctx" not in arguments:
            offenders.append(f"{name}({', '.join(arguments)})")
    assert not offenders, (
        "these caches are keyed without a ScenarioContext, so a result computed "
        f"for one cyclone can be served for another: {offenders}. Add the context "
        "as the only argument, or add the function to CONTEXT_FREE_CACHES with a reason."
    )


def test_every_context_free_cache_has_a_reason() -> None:
    """An unjustified exception is indistinguishable from an oversight."""
    for name, reason in CONTEXT_FREE_CACHES.items():
        assert reason and len(reason) > 20, f"{name} has no real reason"


def test_the_surge_law_is_still_one_line_of_arithmetic() -> None:
    """The deterministic law is authoritative and must not have grown features."""
    from backend.simulation.surge import surge_for_wind

    for wind in (40.0, 115.0, 222.0):
        assert surge_for_wind(wind) == pytest.approx(1.2 * (wind / 115.0) ** 2)


def test_the_ml_layer_is_absent_from_the_surge_path() -> None:
    """The ML estimate is a second, separately labelled figure. Never this one.

    A future change that routes a model output into `predict_surge` would break
    the separation the whole architecture rests on, and nothing else would fail.
    """
    from backend.simulation import surge as surge_module

    import ast

    # Imports and executable symbol references only. The module's docstring
    # explains at length why the regression was replaced, and a text search
    # would forbid the explanation as well as the thing it warns against.
    tree = ast.parse(Path(surge_module.__file__).read_text())
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            referenced.add(node.module)
            referenced.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            referenced.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            referenced.add(node.func.id)

    for forbidden in ("storm_peak", "landfall_intensity", "sklearn", "LinearRegression"):
        assert forbidden not in referenced, (
            f"surge.py references {forbidden!r}. The deterministic surge law is "
            f"authoritative and must not depend on a model."
        )


def test_the_default_context_is_remal_and_the_top_band() -> None:
    """Nothing about the existing default behaviour changed."""
    ctx = ScenarioContext()
    assert ctx.cyclone_id == DEFAULT_CYCLONE_ID
    assert ctx.scenario_id == "cat6"
    assert ctx.wind_kmph() == 222.0