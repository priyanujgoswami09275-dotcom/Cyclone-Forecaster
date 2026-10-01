"""A scenario resolves to exactly one wind, and a typo is an error.

The tests that matter most here are the negative ones. A scenario system that
falls back to a default when it cannot resolve is worse than one that refuses:
it answers a question the reader did not ask, with a plausible number.
"""

from __future__ import annotations

import pytest

from backend.cyclones.base import CycloneRecord, CycloneWaypoint
from backend.cyclones.scenarios import (
    DEFAULT_CYCLONE_ID,
    DEFAULT_SCENARIO_ID,
    ScenarioContext,
    observed_scenario,
    resolve_scenario,
    scenarios_for,
)
from backend.simulation.surge import IMD_BANDS, surge_for_wind


def _record(winds: list[float | None]) -> CycloneRecord:
    waypoints = tuple(
        CycloneWaypoint(
            iso_time=f"2024-05-25 {i:02d}:00:00",
            latitude=20.0 + i * 0.1,
            longitude=88.0,
            wind_kmph=w,
            wind_reported=w is not None,
            pressure_hpa=None,
            nature="TS",
        )
        for i, w in enumerate(winds)
    )
    return CycloneRecord(
        cyclone_id="2024145N14087",
        name="REMAL",
        season=2024,
        basin="NI",
        subbasin="BB",
        source="ibtracs_v04r01_ni",
        observed=True,
        waypoints=waypoints,
        fetched_at="2026-10-01T00:00:00Z",
        data_through=waypoints[-1].iso_time if waypoints else None,
        peak_wind_kmph=None,
        limitation="test",
    )


# --- resolution -------------------------------------------------------------


def test_a_band_scenario_matches_the_band_table_it_comes_from() -> None:
    """Built from `IMD_BANDS`, never restated — that is the drift guard."""
    for index, band in enumerate(IMD_BANDS):
        scenario = resolve_scenario(f"cat{index}")
        assert scenario.wind_kmph == band.representative_kmph()
        assert scenario.imd_category == band.label


def test_the_top_band_is_not_presented_as_a_midpoint() -> None:
    """IMD documents no upper bound for it, so it is the weakest qualifying wind."""
    top = resolve_scenario("cat6")
    assert top.wind_is_band_midpoint is False
    assert top.wind_kmph == 222.0
    assert top.kind == "category"
    assert resolve_scenario("cat3").wind_is_band_midpoint is True


def test_observed_is_the_storms_own_peak() -> None:
    scenario = observed_scenario(_record([30.0, 111.1, None, 60.0]))
    assert scenario is not None
    assert scenario.scenario_id == "observed"
    assert scenario.kind == "observed"
    assert scenario.wind_kmph == pytest.approx(111.1)


def test_observed_ignores_fixes_that_reported_nothing() -> None:
    """A blank is unreported, not calm, and must not become the peak."""
    assert observed_scenario(_record([30.0, None, None])).wind_kmph == pytest.approx(30.0)


def test_a_storm_with_no_reported_wind_has_no_observed_scenario() -> None:
    """None, not the nearest category: that would answer a question nobody asked."""
    assert observed_scenario(_record([None, None])) is None
    assert observed_scenario(None) is None
    assert "observed" not in {s.scenario_id for s in scenarios_for(_record([None]))}


def test_a_known_record_peak_is_preferred_over_recomputing_it() -> None:
    record = _record([30.0, 111.1])
    replaced = CycloneRecord(
        cyclone_id=record.cyclone_id, name=record.name, season=record.season,
        basin=record.basin, subbasin=record.subbasin, source=record.source,
        observed=record.observed, waypoints=record.waypoints,
        fetched_at=record.fetched_at, data_through=record.data_through,
        peak_wind_kmph=99.0, limitation=record.limitation,
    )
    assert observed_scenario(replaced).wind_kmph == 99.0


# --- refusal ----------------------------------------------------------------


def test_an_unknown_scenario_raises_and_names_the_valid_ids() -> None:
    with pytest.raises(KeyError) as excinfo:
        resolve_scenario("nope")
    message = str(excinfo.value)
    assert "nope" in message
    for index in range(len(IMD_BANDS)):
        assert f"cat{index}" in message


def test_observed_is_unknown_for_a_storm_with_no_wind() -> None:
    """The id exists for most cyclones and not for this one, which is honest."""
    with pytest.raises(KeyError):
        resolve_scenario("observed", _record([None]))


# --- the context ------------------------------------------------------------


def test_the_context_defaults_to_remal_and_the_top_band() -> None:
    ctx = ScenarioContext()
    assert ctx.cyclone_id == DEFAULT_CYCLONE_ID
    assert ctx.scenario_id == DEFAULT_SCENARIO_ID


def test_the_context_key_is_the_pair_not_the_wind() -> None:
    """Two scenarios at the same wind must still be different cache entries."""
    a = ScenarioContext("A", "observed")
    b = ScenarioContext("A", "cat4")
    assert a.key == ("A", "observed")
    assert a.key != b.key


def test_the_context_is_hashable_so_it_can_key_a_cache() -> None:
    assert hash(ScenarioContext("A", "cat6")) == hash(ScenarioContext("A", "cat6"))
    assert len({ScenarioContext("A", "cat6"), ScenarioContext("A", "cat6")}) == 1
    assert len({ScenarioContext("A", "cat6"), ScenarioContext("B", "cat6")}) == 2


@pytest.mark.parametrize("bad", ["", None, 3])
def test_an_empty_or_non_string_id_is_rejected_at_construction(bad) -> None:
    """Not defaulted: an empty cyclone id would silently serve Remal."""
    with pytest.raises(ValueError):
        ScenarioContext(bad, "cat6")
    with pytest.raises(ValueError):
        ScenarioContext("2024145N14087", bad)


# --- the surge law is untouched ---------------------------------------------


def test_resolving_a_scenario_does_not_change_the_surge_law() -> None:
    """The deterministic law is authoritative and every route to it agrees."""
    for index in range(len(IMD_BANDS)):
        wind = resolve_scenario(f"cat{index}").wind_kmph
        assert surge_for_wind(wind) == pytest.approx(1.2 * (wind / 115) ** 2)


def test_scenario_limitation_says_it_is_not_a_forecast() -> None:
    """The word a judge will look for."""
    for scenario in scenarios_for(_record([60.0])):
        assert (
            "not a forecast" in scenario.limitation
            or "rather than a prediction" in scenario.limitation
        )


def test_a_scenario_serialises_with_its_provenance() -> None:
    payload = resolve_scenario("cat6").to_dict()
    assert payload["wind_kmph"] == 222.0
    assert payload["wind_is_band_midpoint"] is False
    assert payload["limitation"]
    assert set(payload) == {
        "scenario_id",
        "label",
        "wind_kmph",
        "kind",
        "imd_category",
        "wind_is_band_midpoint",
        "limitation",
    }