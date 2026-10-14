"""The prompt must carry per-locality facts so the model cannot invent geography."""

from backend.ai.advisory import SYSTEM_PROMPT, build_prompt


def allocation_with_facts() -> dict:
    return {
        "allocation": [
            {"node": "namkhana", "population": 1200,
             "assignments": [{"shelter": "DEMO Shelter A", "people": 1200, "distance_km": 5.9}]},
            {"node": "kakdwip", "population": 3400,
             "assignments": [{"shelter": "DEMO Shelter B", "people": 3400, "distance_km": 12.3}]},
        ],
        "shelter_status": {"is_demo_data": True},
        "unmet_demand": 0,
        "capacity_basis": "demo",
    }


def surge_zone() -> dict:
    return {"imd_category": "Super Cyclonic Storm", "wind_kmph": 222,
            "surge_m": 4.47, "final_land_area_km2": 2680.22}


def exposure() -> dict:
    return {"hospitals": {"count": 12}, "substations": {"count": 22},
            "roads_cut_off": {"count": 251}}


class TestLocalityFactsInPrompt:
    def test_one_fact_line_per_locality(self):
        prompt = build_prompt(surge_zone(), exposure(), allocation_with_facts())
        assert "LOCALITY FACTS (estimates)" in prompt
        assert "- namkhana: estimated exposed people 1200 (estimate)" in prompt
        assert "assigned DEMO Shelter A" in prompt
        assert "5.9 km" in prompt
        assert "- kakdwip: estimated exposed people 3400 (estimate)" in prompt
        assert "assigned DEMO Shelter B" in prompt
        assert "12.3 km" in prompt

    def test_system_prompt_has_rule_11(self):
        assert "11." in SYSTEM_PROMPT or "Rule 11" in SYSTEM_PROMPT
        assert "LOCALITY FACTS" in SYSTEM_PROMPT
        assert "no figure available" in SYSTEM_PROMPT
        assert "Do not describe terrain" in SYSTEM_PROMPT or "do not describe terrain" in SYSTEM_PROMPT
