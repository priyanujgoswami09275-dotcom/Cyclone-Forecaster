"""The API surface: cyclone and scenario addressing, provenance everywhere.

The tests that matter are the ones about what a response must *say* about
itself. A number without its provenance is a number a client cannot interpret —
it cannot tell a category default from a chosen scenario from another storm's
figure, and those are three different things that happen to share a shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cyclones.base import LiveStatus
from backend.cyclones.registry import registry
from backend.cyclones.scenarios import DEFAULT_CYCLONE_ID, scenarios_for
from backend.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

#: A second real storm, so "a different cyclone" is a real storm and not a
#: fixture. Chosen from the catalogue rather than invented.
OTHER_CYCLONE_ID = "1970324N05143"


# --------------------------------------------------------------------------
# GET /cyclones
# --------------------------------------------------------------------------


def test_cyclones_lists_historical_and_states_its_source():
    response = client.get("/cyclones")
    assert response.status_code == 200
    body = response.json()

    assert body["source"]["dataset"] == "IBTrACS v04r01"
    assert body["source"]["basin_filter"] == "NI"
    assert body["source"]["generated_from"] == "ibtracs.NI.list.v04r01.csv"
    assert body["limitation"]
    assert body["generated_at"]

    ids = [c["cyclone_id"] for c in body["cyclones"]]
    assert "2024145N14087" in ids
    # Remal stays the default, so the first entry is the case study.
    assert ids[0] == "2024145N14087"
    assert len(ids) == len(set(ids)), "a storm is listed twice"


def test_cyclones_entries_carry_what_a_client_needs_to_address_them():
    body = client.get("/cyclones").json()
    by_id = {c["cyclone_id"]: c for c in body["cyclones"]}
    remal = by_id["2024145N14087"]

    for field in (
        "cyclone_id",
        "name",
        "season",
        "basin",
        "subbasin",
        "peak_wind_kmph",
        "waypoint_count",
        "is_case_study",
    ):
        assert field in remal, field

    assert remal["name"] == "REMAL"
    assert remal["season"] == 2024
    assert remal["basin"] == "NI"
    assert remal["waypoint_count"] == 40
    assert remal["is_case_study"] is True
    assert remal["peak_wind_kmph"] == pytest.approx(111.1, abs=0.05)

    # Only the case study is flagged, and it is flagged on exactly one entry.
    flagged = [c["cyclone_id"] for c in body["cyclones"] if c["is_case_study"]]
    assert flagged == ["2024145N14087"]


def test_cyclones_is_sorted_case_study_first_then_newest():
    body = client.get("/cyclones").json()
    seasons = [c["season"] for c in body["cyclones"]]
    assert seasons[0] == 2024
    assert seasons[1:] == sorted(seasons[1:], reverse=True)


def test_cyclones_count_matches_the_catalogue():
    """The endpoint and the catalogue must not disagree about how many storms."""
    body = client.get("/cyclones").json()
    assert len(body["cyclones"]) == len(registry().historical())


# --------------------------------------------------------------------------
# GET /cyclones/{id}/track
# --------------------------------------------------------------------------


def test_a_cyclone_track_is_the_same_shape_as_the_default_track():
    """The existing map code must be able to consume either."""
    default = client.get("/track").json()
    other = client.get(f"/cyclones/{OTHER_CYCLONE_ID}/track").json()

    assert set(default) == set(other), (
        "the two track endpoints must agree on their shape"
    )
    for field in ("name", "season", "source", "wind_units", "timezone",
                  "waypoint_count", "waypoints", "path", "disclosure"):
        assert field in other, field

    assert other["cyclone"]["cyclone_id"] == OTHER_CYCLONE_ID
    assert other["name"] != "REMAL"
    assert other["waypoint_count"] == len(other["waypoints"])
    assert other["waypoint_count"] == len(other["path"])


def test_the_default_track_names_its_cyclone():
    track = client.get("/track").json()
    assert track["cyclone"]["cyclone_id"] == "2024145N14087"
    assert track["name"] == "REMAL"


def test_an_unknown_cyclone_track_is_a_400_not_the_default():
    response = client.get("/cyclones/not-a-storm/track")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "not-a-storm" in detail
    assert "610" in detail, "the error should say how many storms there are"


# --------------------------------------------------------------------------
# GET /scenarios
# --------------------------------------------------------------------------


def test_scenarios_lists_what_a_client_may_ask_for():
    body = client.get("/scenarios").json()
    assert body["cyclone_id"] == "2024145N14087"
    assert body["limitation"]

    # `observed` is first for a cyclone that has one, so the expected order is
    # this cyclone's own scenario set — not the band-only set `scenarios_for(None)`
    # returns for a cyclone nobody named.
    # `get(None)` is `None`, not the default — the default is a real id.
    expected = [
        s.scenario_id for s in scenarios_for(registry().get(DEFAULT_CYCLONE_ID))
    ]
    ids = [s["scenario_id"] for s in body["scenarios"]]
    assert ids == expected
    assert ids[0] == "observed"
    assert "cat6" in ids

    for scenario in body["scenarios"]:
        for field in (
            "scenario_id",
            "label",
            "wind_kmph",
            "imd_category",
            "kind",
            "limitation",
        ):
            assert field in scenario, field


def test_scenarios_are_scoped_to_the_cyclone_asked_for():
    body = client.get(f"/scenarios?cyclone_id={OTHER_CYCLONE_ID}").json()
    assert body["cyclone_id"] == OTHER_CYCLONE_ID
    # The scenario set is the same; the cyclone it applies to is not.
    assert [s["scenario_id"] for s in body["scenarios"]] == [
        s.scenario_id for s in scenarios_for(registry().get(OTHER_CYCLONE_ID))
    ]


def test_scenarios_reject_an_unknown_cyclone():
    response = client.get("/scenarios?cyclone_id=nope")
    assert response.status_code == 400


# --------------------------------------------------------------------------
# GET /live-cyclone
# --------------------------------------------------------------------------


def test_live_cyclone_reports_live_unavailable_rather_than_substituting():
    """A state, not an error — and never a historical stand-in."""
    response = client.get("/live-cyclone")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] in ("available", "live_unavailable", "no_active_storm")
    assert body["checked_at"]
    assert body["reason"]
    assert body["source"]
    assert body["limitation"]
    assert isinstance(body["endpoints"], list) and body["endpoints"]

    if body["status"] != "available":
        assert body["cyclone"] is None, "a historical storm was substituted"
        assert "historical" in body["reason"].lower()


def test_live_cyclone_never_5xx_for_an_unreachable_source(monkeypatch):
    """An unreachable feed is a fact about the feed, not a server fault."""
    from backend.cyclones import registry as registry_module

    def boom(self):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(
        registry_module.AtcfLiveSource, "probe", boom, raising=True
    )
    response = client.get("/live-cyclone")
    assert response.status_code == 200
    assert response.json()["status"] == "live_unavailable"


def test_live_cyclone_serves_a_real_storm_when_the_source_answers(monkeypatch):
    """The available branch is reachable, so it is not untested prose."""
    from backend.cyclones import registry as registry_module

    # `probe` is async: `live_status` does `asyncio.run(self.source.probe())`,
    # and a sync function there raises, which the registry reads as unavailable.
    async def fake_probe(self):
        return LiveStatus(
            status="available",
            source=self.identifier,
            http_status=200,
            reason="a live fix was parsed",
            checked_at="2026-10-01T00:00:00Z",
            endpoints=self.endpoints,
            cyclone={
                "cyclone_id": "2026001N10060",
                "name": "TEST",
                "latitude": 18.0,
                "longitude": 88.0,
                "wind_kmph": 90.0,
                "pressure_hpa": 990.0,
                "observed_at": "2026-10-01T00:00:00Z",
            },
        )

    monkeypatch.setattr(
        registry_module.AtcfLiveSource, "probe", fake_probe, raising=True
    )
    body = client.get("/live-cyclone").json()
    assert body["status"] == "available"
    assert body["cyclone"]["cyclone_id"] == "2026001N10060"
    assert body["cyclone"]["wind_kmph"] == 90.0


# --------------------------------------------------------------------------
# GET /comparison
# --------------------------------------------------------------------------


def test_comparison_reports_each_cyclone_separately():
    response = client.get(
        "/comparison",
        params={"category": 6, "cyclone_ids": "2024145N14087,1970324N05143"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["limitation"]
    assert body["generated_at"]
    ids = [c["cyclone_id"] for c in body["cyclones"]]
    assert ids == ["2024145N14087", "1970324N05143"]

    for entry in body["cyclones"]:
        for field in ("cyclone_id", "name", "season", "scenario_id",
                      "wind_kmph", "surge_m", "flood_area_km2",
                      "hospitals_exposed", "substations_exposed",
                      "roads_cut_off"):
            assert field in entry, field

    # The deltas are between the two, and are labelled as such.
    assert body["deltas"]
    assert body["deltas"]["between"] == ["2024145N14087", "1970324N05143"]


def test_comparison_defaults_to_the_case_study():
    body = client.get("/comparison", params={"category": 6}).json()
    assert [c["cyclone_id"] for c in body["cyclones"]] == ["2024145N14087"]


def test_comparison_rejects_an_unknown_cyclone():
    response = client.get(
        "/comparison", params={"category": 6, "cyclone_ids": "nope"}
    )
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_comparison_needs_at_least_two_cyclones_for_a_delta():
    """One cyclone has no delta; saying so is better than emitting a zero."""
    body = client.get("/comparison", params={"category": 6}).json()
    assert body["deltas"]["between"] == ["2024145N14087"]
    assert "delta" not in json.dumps(body["deltas"]) or body["deltas"].get(
        "note"
    ), "a single-cyclone comparison must not present a delta as if it had one"


# --------------------------------------------------------------------------
# Existing endpoints, unchanged defaults
# --------------------------------------------------------------------------


def test_existing_endpoints_still_work():
    """The pre-existing surface, unchanged by the new endpoints.

    `/routes` and `/allocation` take a required `category`, so they are called
    with one. The plan's version of this test listed them bare and would have
    failed on a 422 — the intent is "these still work", which means giving them
    what they require.
    """
    for path in (
        "/categories",
        "/track",
        "/exposure?category=6",
        "/routes?category=6&origin=kakdwip",
        "/allocation?category=6",
        "/surge-zone?category=6",
    ):
        assert client.get(path).status_code == 200, path


def test_exposure_defaults_are_unchanged_after_the_rekey():
    body = client.get("/exposure", params={"category": 6}).json()
    assert body["hospitals"]["count"] == 12
    assert body["substations"]["count"] == 22
    assert body["roads_cut_off"]["count"] == 251
    assert body["surge"]["surge_m"] == pytest.approx(4.4719, abs=1e-4)


def test_a_second_cyclone_at_the_same_scenario_is_served_not_cached():
    a = client.get(
        "/exposure", params={"category": 6, "cyclone_id": "2024145N14087"}
    ).json()
    b = client.get(
        "/exposure", params={"category": 6, "cyclone_id": OTHER_CYCLONE_ID}
    ).json()
    assert a["cyclone"]["cyclone_id"] == "2024145N14087"
    assert b["cyclone"]["cyclone_id"] == OTHER_CYCLONE_ID


def test_an_unknown_scenario_is_a_400_naming_the_valid_ids():
    response = client.get("/exposure", params={"category": 6, "scenario_id": "nope"})
    assert response.status_code == 400
    assert "cat6" in response.json()["detail"]


def test_every_response_carries_its_provenance():
    for path in (
        "/exposure?category=6",
        "/cyclones",
        "/scenarios",
        "/live-cyclone",
        "/comparison?category=6",
        "/surge-zone?category=6",
        "/allocation?category=6",
        "/routes?category=6&origin=kakdwip",
    ):
        body = client.get(path).json()
        assert "limitation" in body, path
        assert "generated_at" in body, path


def test_provenance_names_the_pair_it_computed():
    body = client.get(
        "/exposure", params={"category": 6, "cyclone_id": OTHER_CYCLONE_ID}
    ).json()
    # Merged at the top level, not nested: a client should not have to know
    # which endpoints hide their provenance one level down.
    assert body["cyclone_id"] == OTHER_CYCLONE_ID
    assert body["scenario_id"] == "cat6"
    assert body["wind_kmph"] == pytest.approx(222.0, abs=0.5)
    assert body["cyclone"]["cyclone_id"] == OTHER_CYCLONE_ID


def test_scenario_id_wins_over_category_and_says_so():
    body = client.get(
        "/exposure", params={"category": 2, "scenario_id": "cat4"}
    ).json()
    assert body["scenario_id"] == "cat4"
    # cat4 is a Very Severe Cyclonic Storm at 142 kmph, the band midpoint.
    assert body["wind_kmph"] == pytest.approx(142.0, abs=0.5)


# --------------------------------------------------------------------------
# The track and the catalogue must not disagree
# --------------------------------------------------------------------------


def test_the_two_track_endpoints_agree_about_remal():
    """The defect this task exists to close: two Remals, 11.1 kmph apart."""
    default = client.get("/track").json()
    by_id = client.get("/cyclones/2024145N14087/track").json()

    assert default["waypoint_count"] == by_id["waypoint_count"] == 40
    assert [w["timestamp"] for w in default["waypoints"]] == [
        w["timestamp"] for w in by_id["waypoints"]
    ]
    assert default["cyclone"]["peak_wind_kmph"] == pytest.approx(111.1, abs=0.05)
    assert by_id["cyclone"]["peak_wind_kmph"] == pytest.approx(111.1, abs=0.05)


def test_the_track_endpoint_serves_the_regenerated_file():
    """40 fixes, not the 19 the v04r00 file carried."""
    track = client.get("/track").json()
    assert track["source"] == "IBTrACS v04r01"
    assert track["waypoint_count"] == 40
    # RFC 3339, the same format /track emits — a client must not have to accept
    # two spellings of the same field.
    stamps = [w["timestamp"] for w in track["waypoints"]]
    assert stamps[0] == "2024-05-23T12:00:00Z"
    assert stamps[-1] == "2024-05-28T06:00:00Z"
    # The five fixes that used to read 0.0 now carry real winds.
    by_time = {w["timestamp"]: w for w in track["waypoints"]}
    for stamp, knots in (
        ("2024-05-27T03:00:00Z", 48),
        ("2024-05-27T06:00:00Z", 45),
        ("2024-05-27T09:00:00Z", 40),
        ("2024-05-27T12:00:00Z", 35),
        ("2024-05-27T15:00:00Z", 33),
    ):
        assert by_time[stamp]["wind_kt"] == pytest.approx(knots, abs=0.05)
        assert by_time[stamp]["wind_reported"] is True


# --------------------------------------------------------------------------
# The ML layer is surfaced, and labelled as what it is
# --------------------------------------------------------------------------


def test_the_ml_estimate_is_available_and_says_it_is_not_a_prediction():
    """Task 6's negative result must be visible, not hidden."""
    from backend.ml.storm_peak_intensity import estimate_for

    estimate = estimate_for((15.0, 300.0, 80.0, 21.5, 88.0, 5.0, 120.0))
    payload = estimate.to_dict()

    assert payload["estimate_source"] == "median_baseline"
    assert payload["is_a_prediction"] is False
    assert payload["beats_baseline"] is False
    assert payload["estimate_kt"] == pytest.approx(50.0, abs=0.5)
    assert payload["model_kt_unused"] is not None
    assert "NOT a prediction" in payload["limitation"]
    assert "not the surge figure" in payload["limitation"]


def test_the_ml_module_never_reaches_the_surge_law():
    """AST, not grep: the deterministic law must stay authoritative."""
    import ast

    source = (REPO_ROOT / "backend" / "ml" / "storm_peak_intensity.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "simulation" not in node.module, node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "simulation" not in alias.name, alias.name


def test_the_surge_law_is_untouched_by_the_ml_layer():
    from backend.simulation.surge import surge_for_wind

    assert surge_for_wind(115) == pytest.approx(1.2)
    assert surge_for_wind(230) == pytest.approx(4.8)


# --------------------------------------------------------------------------
# The figure guard is deliberately NOT applied to main.py
# --------------------------------------------------------------------------
#
# `tests/figure_guard.py` exists because six hand-counted figures went unnoticed
# in one module. Applying it to `main.py` retroactively would require pinning
# several hundred figures that predate it (status codes, timeouts, band
# thresholds, cache sizes) — a real piece of work, but not this task's, and
# doing it here would bury the endpoint work in an unrelated diff.
#
# Recorded as a known gap rather than skipped silently: the guard is available
# and every NEW module in this project uses it (`test_open_meteo.py`,
# `test_storm_peak_intensity.py`). `main.py` is the one large module that does
# not, and the reason is its age, not a judgement that its figures are safe.
#
# What IS pinned here instead: every figure this task added to main.py, via the
# endpoint tests above (40 fixes, 111.1 kmph, 142 kmph, 222 kmph, 610 storms).
