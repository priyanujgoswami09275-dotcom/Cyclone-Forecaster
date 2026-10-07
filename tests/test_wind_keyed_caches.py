"""Two storms at one wind are one computation; the ids they carry are not.

Every input to `run_flood_model` is the resolved wind, so two cyclones at the
same band would recompute a byte-identical raster. The lru_caches are therefore
keyed on the exact resolved `wind_kmph` float; the (cyclone, scenario) pair
stays at the call sites and in `_provenance(ctx)`. Sharing is correct when the
value is the same; the response's ids are what say whose answer it is.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.cyclones.base import CycloneRecord
from backend.cyclones.scenarios import ScenarioContext
from backend.main import (
    allocation_for_scenario,
    flood_for_scenario,
    flood_for_wind,
    populations_for_scenario,
    shelters_for_scenario,
)

client = TestClient(main.app)

REMAL = "2024145N14087"
OTHER = "1970324N05143"
FABRICATED = "IO992026"  # not a real SID; monkeypatched into the registry


def test_two_cyclones_at_cat6_cost_one_flood_computation(monkeypatch) -> None:
    """Count the raster runs, don't infer them from timings."""
    calls: list[float] = []
    real = main.run_flood_model

    def counting(wind):
        calls.append(wind)
        return real(wind)

    monkeypatch.setattr(main, "run_flood_model", counting)
    flood_for_wind.cache_clear()

    a = flood_for_scenario(ScenarioContext(REMAL, "cat6"))
    b = flood_for_scenario(ScenarioContext(OTHER, "cat6"))
    assert a is b, "same wind ⇒ the same flood object — the raster ran once"
    assert calls == [222.0], f"exactly one raster run, at the shared wind: {calls}"


def test_populations_shelters_and_allocation_are_wind_keyed() -> None:
    """Each layer's only inputs are wind-derived (flood → pop → shelter →
    allocation), so they all share by wind rather than paying per storm."""
    for fn in (populations_for_scenario, shelters_for_scenario, allocation_for_scenario):
        a = fn(ScenarioContext(REMAL, "cat6"))
        b = fn(ScenarioContext(OTHER, "cat6"))
        assert a is b, fn.__name__


def test_responses_carry_their_own_ids_not_the_first_askers() -> None:
    """Shared computation, distinct provenance — the isolation a judge can see."""
    remal = client.get("/surge-zone", params={"category": 6, "cyclone_id": REMAL}).json()
    other = client.get("/surge-zone", params={"category": 6, "cyclone_id": OTHER}).json()
    assert remal["cyclone_id"] == REMAL
    assert other["cyclone_id"] == OTHER
    # Same wind ⇒ same computation: the polygon bytes are identical.
    assert remal["geojson"] == other["geojson"]


def test_observed_and_a_band_at_the_same_wind_are_two_responses(monkeypatch) -> None:
    """The pair shares the computation; it must never share the identity seams.

    No catalogue storm peaks at exactly a band wind, so the storm here is a
    fixture record at 222.0: the observed scenario and the cat6 band resolve
    to the same float, and both responses must carry their own scenario id.
    """
    real_registry = main.registry()
    record = CycloneRecord(
        cyclone_id=FABRICATED,
        name="Observed 222 Fixture",
        season=2026,
        basin="NI",
        subbasin="BB",
        source="test",
        observed=True,
        waypoints=(),
        fetched_at="2026-10-07T00:00:00Z",
        data_through=None,
        peak_wind_kmph=222.0,
        limitation="fixture",
    )

    class _Stub:
        def get(self, cyclone_id):
            return record if cyclone_id == FABRICATED else real_registry.get(cyclone_id)

    monkeypatch.setattr(main, "registry", lambda: _Stub())

    band = client.get(
        "/surge-zone",
        params={"category": 6, "cyclone_id": FABRICATED, "scenario_id": "cat6"},
    ).json()
    obs = client.get(
        "/surge-zone",
        params={"category": 6, "cyclone_id": FABRICATED, "scenario_id": "observed"},
    ).json()

    assert band["scenario_id"] == "cat6"
    assert obs["scenario_id"] == "observed"
    assert band["cyclone_id"] == obs["cyclone_id"] == FABRICATED
    # The point of wind-keying: one computation, one raster, two (correctly
    # labelled) responses.
    assert band["geojson"] == obs["geojson"]
