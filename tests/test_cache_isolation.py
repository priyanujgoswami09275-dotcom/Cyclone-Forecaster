"""Cache keys: sharing a value is correct; sharing an identity is not.

## What changed, and why the assertions flipped

These caches used to be keyed on `(cyclone_id, scenario_id)` and the file
asserted that two storms at the same scenario must **not** share an entry. That
was the right instinct pointed at the wrong property. Every input to
`run_flood_model`, and to the population/shelter/allocation layers above it, is
the resolved wind. Two storms at `cat6` are not two different flood extents —
they are one extent computed twice, and the second computation cost a measured
16.6 s to produce a byte-identical result.

The caches are now keyed on the exact resolved `wind_kmph` float, so those
assertions are inverted: same wind **must** share, and the response-level
guarantee moved to `tests/test_wind_keyed_caches.py`, which pins what a client
can actually observe — two storms get separate responses carrying separate
`cyclone_id`s over one shared computation, and an `observed` scenario and a band
resolving to the same wind do the same.

Every other failure mode in this codebase raises. A wrong cache key does not:
it returns a plausible number, a plausible polygon, a map that draws
confidently. So the source-level guard below remains, now covering the key that
is still never legal — a bare category index.

`test_no_lru_cache_in_main_is_keyed_on_a_bare_category` inspects source rather
than behaviour on purpose: the bug class is *a function whose key omits the
strength*, and only a source check catches a function nobody calls yet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.cyclones.scenarios import DEFAULT_CYCLONE_ID, ScenarioContext
from backend.main import (
    allocation_for_scenario,
    flood_for_scenario,
    flood_for_wind,
    surge_for_scenario,
)

MAIN_PY = Path(__file__).resolve().parents[1] / "backend" / "main.py"

# Two real IBTrACS SIDs from different storms in different decades.
REMAL = "2024145N14087"
OTHER = "1970324N05143"


# --- the caches themselves --------------------------------------------------


def test_two_cyclones_at_the_same_scenario_share_the_flood_computation() -> None:
    """The core property, inverted on purpose. Same wind ⇒ same computation.

    Every input to `run_flood_model` is the wind, so a second storm at `cat6`
    is not a different flood extent — it is the *same* extent computed twice.
    The caches are therefore keyed on the resolved wind, and this asserts the
    sharing rather than fighting it.

    What must NOT share is identity, and that is now pinned at the response
    level in `tests/test_wind_keyed_caches.py`: two storms must get separate
    responses carrying separate `cyclone_id`s over one shared computation.
    """
    a = flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    b = flood_for_scenario(ScenarioContext(OTHER, "cat6"))
    assert a is b, "same wind ⇒ one flood object; the raster ran once"


def test_the_same_context_hits_the_cache() -> None:
    """A key that never hits is not a cache; this is the other half of the test."""
    flood_for_wind.cache_clear()
    first = flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    second = flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    assert first is second


def test_the_cache_key_is_the_wind_not_the_context() -> None:
    """Four storms at one band are one entry, because they are one wind.

    The growth assertion this replaced (`currsize == 4` for four contexts at
    cat5) was counting entries, not correctness — under the old ctx key it
    proved the cache was doing redundant work, and nothing else.
    """
    flood_for_wind.cache_clear()
    for cyclone in (REMAL, OTHER, "1970326N10071", "1971124N10093"):
        flood_for_scenario(ScenarioContext(cyclone, "cat5"))
    assert flood_for_wind.cache_info().currsize == 1


def test_a_different_wind_is_a_different_entry() -> None:
    """The other half: the key must still discriminate. One entry is only
    correct because all four contexts resolved to the same wind."""
    flood_for_wind.cache_clear()
    flood_for_scenario(ScenarioContext(REMAL, "cat5"))
    flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    assert flood_for_wind.cache_info().currsize == 2


def test_observed_and_the_band_it_matches_are_different_contexts() -> None:
    """Even when two scenarios resolve to the same wind.

    The caches are keyed on the wind, so sharing the *value* is correct here.
    What must stay distinct is the context itself — it is what carries
    provenance into `_provenance(ctx)` and therefore into every response, and
    a context whose `__eq__`/`__hash__` collapsed two scenarios would make
    those responses lie about which scenario produced them.
    `tests/test_wind_keyed_caches.py` pins the response half of this.
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


def test_allocation_shares_the_wind_key() -> None:
    """Populations and capacities both descend from the wind; so does this."""
    a = allocation_for_scenario(ScenarioContext(REMAL, "cat6"))
    b = allocation_for_scenario(ScenarioContext(OTHER, "cat6"))
    assert a is b, "same wind ⇒ same assignment; the LP ran once"


# --- the source-level guard -------------------------------------------------

_CACHE_FN = re.compile(r"@lru_cache\([^)]*\)\s*\ndef\s+(\w+)\(([^)]*)\)", re.S)

#: Caches that legitimately take neither a context nor a wind, each with the
#: reason. Growing this list needs a reason in the entry, which is the point.
CONTEXT_FREE_CACHES = {
    "_component_index": "the road graph, which does not depend on any storm",
    "_catalogue": "lives in registry.py, keyed on nothing because it is the list",
    "_registry_singleton": "a singleton constructor",
    "_raw_rows": "a test helper over the archive file",
}

#: A wind-keyed cache is legitimate — and is now the norm. The key must still be
#: a *wind*, never a category index: `cat6` and a storm whose own observed peak
#: is 222 kmph resolve to the same computation, and two different strengths must
#: not collapse into one entry.
WIND_KEYED_CACHES = {
    "flood_for_wind",
    "populations_for_wind",
    "shelters_for_wind",
    "allocation_for_wind",
}


def _argument_names(params: str) -> list[str]:
    return [
        part.split(":")[0].split("=")[0].strip()
        for part in params.split(",")
        if part.strip()
    ]


def test_no_lru_cache_in_main_is_keyed_on_a_bare_category() -> None:
    """The bug class, caught before anyone calls the function.

    Only a source check does this. A behavioural test proves nothing about a
    cache nobody exercises, and by the time a caller hits it the wrong entry has
    already been served.

    Two legal keys: the full `ScenarioContext`, or the resolved `wind_kmph`
    (see WIND_KEYED_CACHES). What is never legal is a category index — that is
    the key that hands one storm another's figures.
    """
    source = MAIN_PY.read_text()
    offenders = []
    for match in _CACHE_FN.finditer(source):
        name, params = match.group(1), match.group(2)
        if name in CONTEXT_FREE_CACHES or name in WIND_KEYED_CACHES:
            continue
        arguments = _argument_names(params)
        if "ctx" not in arguments:
            offenders.append(f"{name}({', '.join(arguments)})")
    assert not offenders, (
        "these caches are keyed neither on a ScenarioContext nor on a resolved "
        f"wind, so a result computed for one strength can be served for "
        f"another: {offenders}. Add the context, key on wind_kmph, or add the "
        "function to CONTEXT_FREE_CACHES with a reason."
    )


def test_every_wind_keyed_cache_really_takes_only_a_wind() -> None:
    """The exemption is only safe while the signature is exactly one float.

    A `*_for_wind` that grew a second parameter could be quietly keyed on
    something that varies per storm, and this list would keep exempting it.
    """
    source = MAIN_PY.read_text()
    for name in sorted(WIND_KEYED_CACHES):
        match = next(m for m in _CACHE_FN.finditer(source) if m.group(1) == name)
        arguments = _argument_names(match.group(2))
        assert arguments == ["wind_kmph"], (
            f"{name} takes {arguments}; the wind-keyed exemption assumes the "
            "single argument is the resolved wind"
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