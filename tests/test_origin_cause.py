"""The origin's plan entry must follow its computed cause, not a ranking habit.

`_ensure_origin_in_plan` used to stamp every unreachable origin CRITICAL and
append "this is the highest-priority locality in the district at this
intensity" — a ranking nothing computes, pasted onto a cause that may be a
road-data coverage gap rather than flooding. The cause is now structural: the
same branch that writes the reason tags it, and the priority follows the tag.

Three causes, three entries:

- `none`      — reachable. HIGH, unchanged.
- `flood`     — the dry network connects and water closed the route. CRITICAL.
- `road_data` — the committed extract never connected origin and shelter. HIGH,
                and the reasoning says plainly it is a data gap, not a flood
                finding.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.ai.advisory import DistrictAdvisory, EvacuationPriority
from backend.cyclones.scenarios import ScenarioContext
from backend.locations import Locality, all_localities
from backend.simulation.routing import safe_route

client = TestClient(main.app)

REMAL = "2024145N14087"


def a_valid_advisory(localities: list[str]) -> DistrictAdvisory:
    """A plan that satisfies every rule `validate_advisory` checks."""
    return DistrictAdvisory(
        executive_summary=(
            "Conditions over the delta. Shelter capacity figures are "
            "provisional placeholders, not surveyed."
        ),
        evacuation_plan=[
            EvacuationPriority(
                locality_name=name,
                priority_level="CRITICAL",
                reasoning="Estimated exposed population inside the flood extent.",
            )
            for name in localities
        ],
        sms_dispatch_draft="Cyclone alert: evacuate low-lying areas now. Figures provisional.",
        post_landfall_risks="Salinisation of farmland is expected to persist.",
        historical_context="Comparable in wind to Cyclone Amphan (2020).",
    )


def _shelter():
    return SimpleNamespace(name="DEMO Shelter A (Namkhana)", lon=88.45, lat=21.90)


def _facts(**overrides) -> dict:
    facts = {
        "locality": Locality(
            id="kakdwip", name="Kakdwip", lon=88.22, lat=21.75,
            radius_km=5.0, place="town", source="test",
        ),
        "shelter": _shelter(),
        "shelter_basis": "lp",
        "reachable": False,
        "cause": "flood",
        "reason": "endpoint not reachable",
        "length_km": None,
        "in_allocation": False,
    }
    facts.update(overrides)
    return facts


def _plan_without_origin() -> DistrictAdvisory:
    return DistrictAdvisory(
        executive_summary="Summary.",
        evacuation_plan=[
            EvacuationPriority(
                locality_name="Namkhana", priority_level="HIGH", reasoning="r"
            )
        ],
        sms_dispatch_draft="Draft.",
        post_landfall_risks="Risks.",
        historical_context="Context.",
    )


# --- the three causes, on the entry itself ---------------------------------


def test_flood_cause_is_critical_and_claims_no_ranking():
    entry = main._ensure_origin_in_plan(
        _plan_without_origin(), _facts(cause="flood"), {}
    ).evacuation_plan[0]
    assert entry.priority_level == "CRITICAL"
    assert "UNREACHABLE" in entry.reasoning
    assert "off-network" in entry.reasoning
    assert "highest-priority" not in entry.reasoning


def test_road_data_cause_is_high_and_names_the_gap():
    entry = main._ensure_origin_in_plan(
        _plan_without_origin(), _facts(cause="road_data"), {}
    ).evacuation_plan[0]
    assert entry.priority_level == "HIGH", "a data gap is not a flood warning"
    assert "does not connect" in entry.reasoning
    assert "data gap, not a flood finding" in entry.reasoning
    assert "UNREACHABLE" not in entry.reasoning
    assert "highest-priority" not in entry.reasoning


def test_reachable_cause_is_high_and_unchanged():
    entry = main._ensure_origin_in_plan(
        _plan_without_origin(),
        _facts(reachable=True, cause="none", length_km=12.3, reason=""),
        {},
    ).evacuation_plan[0]
    assert entry.priority_level == "HIGH"
    assert "flood-free route" in entry.reasoning


# --- the cause is structural, from the diagnosis's own branches ------------


def _box(west, south, east, north):
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def test_a_flood_severed_route_diagnoses_flood():
    """module_b proves this pair is dry-connected; a box flood severs it.

    The reason is routing's own — the branch must keep it and tag `flood`,
    not paraphrase the cause out of the text.
    """
    flood = SimpleNamespace(frames=[SimpleNamespace(geometry=_box(87.0, 21.0, 90.0, 23.0))])
    graph = main.build_road_graph()
    origin, destination = (88.22, 21.75), (88.45, 21.90)
    route = safe_route(graph, flood.frames[-1].geometry, origin, destination)
    assert not route.reachable, "fixture premise: the box flood severs this pair"
    cause, reason = main._diagnose_cause_and_reason(
        route,
        SimpleNamespace(lon=origin[0], lat=origin[1]),
        SimpleNamespace(lon=destination[0], lat=destination[1]),
        flood,
    )
    assert cause == "flood"
    assert reason == route.reason


def test_a_disconnected_pair_diagnoses_road_data():
    """Kolkata is not in the southern-delta extract: components differ."""
    ctx = ScenarioContext(cyclone_id=REMAL, scenario_id="cat6")
    kolkata = next(l for l in all_localities() if l.id == "kolkata")
    facts = main._origin_facts(ctx, kolkata)
    assert not facts["reachable"]
    assert facts["cause"] == "road_data"


# --- the three causes at the endpoint ----------------------------------------

# Sagar: road_data at every intensity (its assigned shelter is on a component
# the extract does not join to the island). Anantapur: reachable at cat3
# (cause none), flood-cut at cat6. The stubbed model omits the origin so the
# code-built entry — the thing under test — is what the response carries.


@pytest.fixture
def stub_generate(monkeypatch):
    """Key + a valid advisory that omits one locality on request.

    `validate_advisory` demands every allocation locality be named, so the
    stub builds its plan from the allocation's own rows — minus the origin,
    which is the omission `_ensure_origin_in_plan` exists to repair. Returns
    a setter the test calls with the origin's name before posting.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    state = {"omit": None}

    def generate(surge, exposure, allocation, context="", corrections="", cyclone=None):
        nodes = [
            row["node"]
            for row in allocation["allocation"]
            if row["node"] != state["omit"]
        ]
        return a_valid_advisory(nodes)

    monkeypatch.setattr(main, "generate_advisory", generate)
    return lambda name: state.update(omit=name)


def test_sagars_entry_is_high_and_names_the_gap(stub_generate):
    stub_generate("Sagar")
    body = client.post("/advisory?category=6&origin=sagar").json()
    entry = body["advisory"]["evacuation_plan"][0]
    assert entry["locality_name"] == "Sagar"
    assert entry["priority_level"] == "HIGH"
    assert "data gap, not a flood finding" in entry["reasoning"]
    assert "highest-priority" not in entry["reasoning"]
    assert body["generated_for"]["origin_reachable"] is False


def test_a_flood_cut_origin_is_critical(stub_generate):
    stub_generate("Anantapur")
    body = client.post("/advisory?category=6&origin=anantapur").json()
    entry = body["advisory"]["evacuation_plan"][0]
    assert entry["locality_name"] == "Anantapur"
    assert entry["priority_level"] == "CRITICAL"
    assert "UNREACHABLE" in entry["reasoning"]
    assert "highest-priority" not in entry["reasoning"]


def test_a_reachable_origin_is_high(stub_generate):
    stub_generate("Anantapur")
    body = client.post("/advisory?category=3&origin=anantapur").json()
    entry = body["advisory"]["evacuation_plan"][0]
    assert entry["locality_name"] == "Anantapur"
    assert entry["priority_level"] == "HIGH"
    assert "flood-free route" in entry["reasoning"]
