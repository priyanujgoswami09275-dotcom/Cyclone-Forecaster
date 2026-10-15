"""The advisory must label a hypothetical what-if as a simulation.

Observed bug: with Remal and cat5, the advisory said "Cyclone Remal alert:
194 kmph winds, 3.41m surge expected" — Remal's observed peak is ~111 kmph;
194 is the what-if wind. The prompt now carries an explicit SCENARIO block,
SYSTEM_PROMPT rule 10 forbids presenting a band scenario as a forecast, and
the validator rejects text that calls it one.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.ai.advisory import (
    DistrictAdvisory,
    EvacuationPriority,
    SYSTEM_PROMPT,
    build_prompt,
    validate_advisory,
)
from backend.main import _cyclone_block, registry

client = TestClient(main.app)


def allocation() -> dict:
    return {
        "allocation": [
            {"node": "namkhana", "population": 1000,
             "assignments": [{"shelter": "DEMO Shelter A", "people": 1000, "distance_km": 5.9}]},
            {"node": "kakdwip", "population": 2000,
             "assignments": [{"shelter": "DEMO Shelter A", "people": 2000, "distance_km": 8.1}]},
        ],
        "shelter_status": {"is_demo_data": True},
        "unmet_demand": 0,
        "capacity_basis": "demo",
    }


def surge_zone(wind=194, imd="Extremely Severe Cyclonic Storm", surge=3.41) -> dict:
    return {
        "imd_category": imd,
        "wind_kmph": wind,
        "surge_m": surge,
        "final_land_area_km2": 1709.57,
    }


def exposure() -> dict:
    return {
        "hospitals": {"count": 5},
        "substations": {"count": 10},
        "roads_cut_off": {"count": 122},
    }


def cyclone_block(kind="category", wind=194.0, imd="Extremely Severe Cyclonic Storm") -> dict:
    return {
        "cyclone_id": "2024145N14087",
        "name": "REMAL",
        "season": 2024,
        "basin": "NI",
        "subbasin": "BB",
        "peak_wind_kmph": 111.1,
        "waypoint_count": 40,
        "scenario_kind": kind,
        "scenario_wind_kmph": wind,
        "scenario_imd_category": imd,
    }


def advisory_with(summary="SIMULATION: what-if at this strength.", sms="SIMULATION advisory. Placeholder data.") -> DistrictAdvisory:
    return DistrictAdvisory(
        executive_summary=summary,
        evacuation_plan=[
            EvacuationPriority(locality_name="namkhana", priority_level="HIGH", reasoning="Provisional reasoning."),
            EvacuationPriority(locality_name="kakdwip", priority_level="HIGH", reasoning="Provisional reasoning."),
        ],
        sms_dispatch_draft=sms,
        post_landfall_risks="Freshwater flooding expected.",
        historical_context="Comparable to Cyclone Amphan (2020).",
    )


class TestPromptScenarioBlock:
    def test_hypothetical_block_present_for_band(self):
        prompt = build_prompt(
            surge_zone(), exposure(), allocation(),
            cyclone=cyclone_block(kind="category"),
        )
        assert "HYPOTHETICAL SIMULATION" in prompt
        assert "NOT an observation or forecast of REMAL" in prompt
        assert "111.1 kmph" in prompt, "the observed peak must be named"

    def test_observed_block_present_for_observed(self):
        prompt = build_prompt(
            surge_zone(wind=111, imd="Severe Cyclonic Storm", surge=1.12),
            exposure(), allocation(),
            cyclone=cyclone_block(kind="observed", wind=111.1, imd="Severe Cyclonic Storm"),
        )
        assert "OBSERVED WIND of REMAL" in prompt
        assert "HYPOTHETICAL" not in prompt

    def test_band_with_no_reported_peak_does_not_say_none(self):
        """310 of 610 catalogue storms have no reported peak wind; a band
        what-if on one of them must not render the literal 'None'."""
        block = cyclone_block(kind="category")
        block["peak_wind_kmph"] = None
        prompt = build_prompt(surge_zone(), exposure(), allocation(), cyclone=block)
        assert "None" not in prompt
        assert "no reported observed peak" in prompt

    def test_system_prompt_has_rule_10(self):
        assert "10." in SYSTEM_PROMPT and "simulation" in SYSTEM_PROMPT.lower()
        assert "11." in SYSTEM_PROMPT and "LOCALITY FACTS" in SYSTEM_PROMPT


class TestValidatorScenarioKind:
    def test_alert_wording_flagged_for_band(self):
        """The exact observed bug text must be rejected."""
        adv = advisory_with(
            summary="Cyclone Remal alert: 194 kmph winds, 3.41m surge expected",
            sms="REMAL alert. Placeholder data.",
        )
        violations = validate_advisory(adv, allocation(), scenario_kind="category")
        assert any("simulat" in v.lower() for v in violations), (
            f"'alert...expected' without 'simulation' must be flagged; got: {violations}"
        )

    def test_forecast_sms_flagged_for_band(self):
        adv = advisory_with(sms="SIMULATION. REMAL will make landfall soon.")
        violations = validate_advisory(adv, allocation(), scenario_kind="category")
        assert any("forecast" in v.lower() or "landfall" in v.lower() for v in violations)

    def test_simulation_wording_passes_for_band(self):
        adv = advisory_with()
        violations = validate_advisory(adv, allocation(), scenario_kind="category")
        scenario_violations = [v for v in violations if "simulat" in v.lower()]
        assert scenario_violations == [], f"properly labelled simulation must pass; got: {scenario_violations}"

    def test_no_scenario_kind_passes_as_before(self):
        """Backward compat: validate_advisory without scenario_kind still works."""
        adv = advisory_with(summary="Provisional advisory.", sms="Evacuate. Placeholder data.")
        violations = validate_advisory(adv, allocation())
        scenario_violations = [v for v in violations if "simulat" in v.lower()]
        assert scenario_violations == []

    def test_negated_forecast_words_pass_for_band(self):
        """Saying "this is not a forecast" is the honest label rule 10 asks
        for — flagging it would punish the model for complying."""
        for sms in (
            "SIMULATION: not a forecast; a what-if at this strength.",
            "SIMULATION. No forecast has been issued for this storm.",
            "SIMULATION — real landfall damage is not expected to match this.",
        ):
            adv = advisory_with(sms=sms)
            violations = validate_advisory(adv, allocation(), scenario_kind="category")
            word_violations = [v for v in violations if "forecast" in v or "expected" in v]
            assert word_violations == [], (sms, word_violations)

    def test_unnegated_forecast_words_still_flagged_for_band(self):
        for sms in (
            "SIMULATION. The forecast says 194 kmph winds.",
            "SIMULATION. Landfall winds are expected near 194 kmph.",
        ):
            adv = advisory_with(sms=sms)
            violations = validate_advisory(adv, allocation(), scenario_kind="category")
            assert any("forecast language" in v for v in violations), (sms, violations)


class TestCacheKeyIncludesScenarioKind:
    def test_band_and_observed_do_not_share_a_cache_entry(self, monkeypatch):
        """Two scenarios with the same wind but different kinds must NOT
        share an advisory — the text now depends on the scenario kind."""
        main._advisory_cache._store.clear()
        main._advisory_cache._order.clear()
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        from backend.ai.advisory import DistrictAdvisory as DA, EvacuationPriority as EP

        calls = []
        def mock(*args, **kwargs):
            calls.append(1)
            alloc = args[2] if len(args) > 2 else kwargs.get("allocation", {})
            nodes = [r["node"] for r in alloc.get("allocation", [])]
            return DA(
                executive_summary="Provisional simulation advisory with placeholder shelter data.",
                evacuation_plan=[EP(n, "HIGH", "Reachable.") for n in nodes],
                sms_dispatch_draft="SIMULATION advisory. Placeholder shelters.",
                post_landfall_risks="Flooding.",
                historical_context="Cyclone Amphan 2020.",
            ), 1

        monkeypatch.setattr(main, "_generate_with_capacity_retry", mock)

        # Request with scenario_id=observed (kind=observed)
        r1 = client.post("/advisory?category=3&origin=namkhana&scenario_id=observed")
        assert r1.status_code == 200
        assert r1.json()["served_from_cache"] is False

        # Request with scenario_id=cat3 (a band — even if the wind is similar,
        # the kind differs so the cache must miss)
        r2 = client.post("/advisory?category=4&origin=namkhana&scenario_id=cat4")
        assert r2.status_code == 200
        assert r2.json()["served_from_cache"] is False, (
            "a band scenario must not be served from an observed-scenario cache entry"
        )
