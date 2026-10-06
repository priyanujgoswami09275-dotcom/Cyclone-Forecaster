"""Tests for the Gemini advisory layer (Module D).

The schema is the easy half. A `DistrictAdvisory` that validates against its
pydantic model can still be wrong in the ways that matter: quote a shelter
occupancy figure as though it were surveyed, name a locality that is not in
the allocation data, or write a 300-character SMS that nobody can send. Those
are the failures this file targets.

Most of these tests run with Gemini stubbed out, because the properties under
test are properties of *our* code — the prompt, the payload assembly, the
validator, the failure handling — not of the model. The tests that do call
Gemini are marked `requires_key` and are **opt-in**: they run only when
`RUN_LIVE_TESTS=1` *and* `GEMINI_API_KEY` is set, so the suite passes for
anyone who has not set one — and, since 2026-09-28, for anyone who has.

That second condition is the point. The free tier allows 20 calls per day per
project per model (§26 in MEMORY.md), and key-presence gating meant every
`pytest tests/` on a configured machine silently spent quota. Opt-in makes
spending it a deliberate act:

    RUN_LIVE_TESTS=1 venv/bin/pytest tests/test_module_d.py

Budget for 3 live calls per full run of that file.

Note what is NOT tested here: that Gemini is a good writer. If a draft is
grammatical and hits every rule below, it ships. Judging prose quality is a
human job.
"""

from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient
from google.genai.errors import ServerError
from pydantic import ValidationError

from backend import main
from backend.ai import advisory
from backend.ai.advisory import (
    DistrictAdvisory,
    EvacuationPriority,
    build_prompt,
    plan_coverage,
    validate_advisory,
)

API_KEY = os.environ.get("GEMINI_API_KEY")
RUN_LIVE = os.environ.get("RUN_LIVE_TESTS") == "1"
requires_key = pytest.mark.skipif(
    not (RUN_LIVE and API_KEY),
    reason=(
        "live Gemini tests are opt-in: set RUN_LIVE_TESTS=1 (and have "
        "GEMINI_API_KEY set) to run them. Key-presence is not enough — the "
        "free tier allows 20 calls/day/project/model, so having a key in .env "
        "must never be what spends the quota."
    ),
)


def capacity_error(message: str = "This model is currently experiencing high demand.") -> ServerError:
    """A `ServerError` shaped like the 503 UNAVAILABLE the API actually returned."""
    return ServerError(
        503,
        {
            "error": {
                "code": 503,
                "message": message,
                "status": "UNAVAILABLE",
            }
        },
    )


def _advisory_body(client, url: str) -> dict:
    """POST /advisory and return its body — skipping if the model is at capacity.

    `gemini-3.8-flash` is intermittently 503 UNAVAILABLE, and the handler now
    retries three times over 6s (2s + 4s) before admitting it. That is a fact
    about the model's load, not a failure of this code, so it skips with the
    reason rather than flapping the suite red on someone else's outage.
    Anything else — a 502 from a real honesty violation, a 500, or a 429 from
    an exhausted daily quota (§26) — still fails, because that one *is* either
    this code or a limit we should see.
    """
    response = client.post(url)
    if response.status_code == 503:
        detail = str(response.json().get("detail", ""))
        if "capacity" in detail.lower():
            pytest.skip(f"{advisory.ADVISORY_MODEL} is at capacity: {detail}")
    assert response.status_code == 200, response.text
    return response.json()

CATEGORY = 6  # Super Cyclonic Storm — the only band that actually floods

# What the stubbed generate_advisory was handed on the last call. Module-level
# because a fixture's return value is not visible inside sibling test methods.
_CAPTURED: dict = {}


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


@pytest.fixture(scope="module")
def payloads():
    """The three real endpoint payloads, assembled once for the whole module."""
    return {
        "surge": main.surge_zone(CATEGORY),
        "exposure": main.exposure(CATEGORY),
        "allocation": main.allocation(CATEGORY),
    }


# The two localities the synthetic allocation below carries, and the
# in-district one it deliberately omits. Named here so the fixtures and the
# assertions about them cannot drift apart.
SYNTHETIC_LOCALITIES = ["Kakdwip", "Namkhana", "Gosaba"]
OUT_OF_DISTRICT = "Kolkata"


def _synthetic_allocation() -> dict:
    """An allocation with two assigned localities, in the real response shape.

    Why this exists: these tests are about the ADVISORY layer — that the plan
    covers every allocated locality, that out-of-district names are excluded,
    that the prompt names exactly what was allocated. None of that depends on
    the flood model, but all of it depends on the allocation being non-empty.

    It was empty at every category while the IMD thresholds were read as
    knots, because every wind was ~1.85x too low and the flood never cleared
    the DEM's 1 m vertical quantum (see MEMORY.md "Flagged for review" §31).
    The knots bug is fixed and the real allocation is populated again, but
    these tests keep the synthetic allocation: driving them through the real
    endpoint meant they were testing the DEM rather than the advisory logic,
    and any future change to the surge model or the DEM would move them for
    reasons that had nothing to do with what they were written to protect. A
    synthetic allocation makes the
    dependency explicit and keeps the coverage the tests were providing.

    Shape copied from `allocate_shelters`, not invented: node / population /
    assignments[{shelter, people, distance_km}].
    """
    return {
        "category": CATEGORY,
        "localities_evaluated": len(SYNTHETIC_LOCALITIES),
        "allocation": [
            {
                "node": name,
                "population": 12_000,
                "assignments": [
                    {
                        "shelter": "Sagar Island MPCS",
                        "people": 12_000,
                        "distance_km": 4.2,
                    }
                ],
            }
            for name in SYNTHETIC_LOCALITIES
        ],
        "shelter_loads": [
            {
                "shelter": "Sagar Island MPCS",
                "capacity_people": 50_000,
                "assigned": 24_000,
            }
        ],
        "unmet_demand": 0,
        "total_person_km": 100.8,
        "message": "optimal assignment found (HI-GHS linear program)",
        "shelter_status": main.shelter_dataset_status(),
        "capacity_basis": {
            "shelters_are_real": False,
            "rule": (
                "DERIVED capacity, NOT surveyed. Placeholder figures pending "
                "verification against district records."
            ),
        },
        "population_method": main.population_methodology(),
        "is_estimate": True,
    }


@pytest.fixture
def synthetic_allocation_result(monkeypatch):
    """Patch the handler's allocation helper so `/advisory` sees a populated one.

    The three handler-level tests below drive the real `POST /advisory` route,
    so unlike the unit tests they cannot be handed a fixture — they get
    whatever the endpoint computes. With the real allocation empty at every
    category, the stubs were emitting an empty evacuation plan, which the
    validator had no reason to complain about, and the tests passed or failed
    for reasons unrelated to what they were written to check.

    Patching `main.allocation_for_scenario` fixes it at the seam the handler
    actually calls, so everything downstream — prompt building, validation,
    the correction pass, the coverage label — runs for real against a
    non-empty allocation.

    **Renamed from `allocation_for_category` on 2026-10-01.** Every scenario
    cache is now keyed on `(cyclone_id, scenario_id)` rather than a bare
    category, so the helper this fixture patches takes a `ScenarioContext`. The
    seam is the same seam; only its name and its argument moved. What is under
    test is unchanged: a real allocation flowing through the real handler.
    """
    result = {
        "assignment": _synthetic_allocation()["allocation"],
        "shelter_loads": _synthetic_allocation()["shelter_loads"],
        "unmet_demand": 0,
        "total_person_km": 100.8,
        "message": "optimal assignment found (HI-GHS linear program)",
        "status": main.shelter_dataset_status(),
    }
    monkeypatch.setattr(main, "allocation_for_scenario", lambda ctx: result)
    return result


@pytest.fixture(scope="module")
def payloads_with_allocation(payloads):
    """`payloads` with a non-empty allocation substituted in.

    `surge` and `exposure` stay real, because the prompt's interaction with
    those numbers is part of what is under test.
    """
    return {**payloads, "allocation": _synthetic_allocation()}


def a_valid_advisory(localities: list[str], omit: str | None = None) -> DistrictAdvisory:
    """A hand-built advisory that satisfies every rule validate_advisory checks.

    `omit` drops one locality from the plan. The origin guarantee under test is
    "if the model did not mention the origin, code adds it" — so a test of that
    guarantee has to *arrange* for the model to omit it. Building the plan from
    the allocation's own node list used to arrange that implicitly, back when
    the requesting locality had no allocation row. It no longer does: at
    category 6 the allocation carries 21 rows including Sagar, so a plan built
    from those names mentions the origin and the code path is never reached.
    Pass `omit` explicitly instead of relying on which rows happen to exist.
    """
    if omit is not None:
        localities = [name for name in localities if name != omit]
    return DistrictAdvisory(
        executive_summary=(
            "Super Cyclonic Storm conditions over the delta. Shelter capacity "
            "figures are provisional placeholders, not surveyed."
        ),
        evacuation_plan=[
            EvacuationPriority(
                locality_name=name,
                priority_level="CRITICAL",
                reasoning="Estimated exposed population inside the flood extent.",
            )
            for name in localities
        ],
        sms_dispatch_draft="Cyclone alert: evacuate low-lying areas now. Shelter figures provisional.",
        post_landfall_risks=(
            "Salinisation of farmland and freshwater contamination of wells are "
            "expected to persist for several weeks after the water recedes."
        ),
        historical_context=(
            "Comparable in wind speed to Cyclone Amphan (2020), which made "
            "landfall further north in West Bengal."
        ),
    )


# --------------------------------------------------------------------------
# Schema and prompt assembly — no network, no key
# --------------------------------------------------------------------------


class TestSchema:
    def test_field_is_locality_name_not_block_name(self):
        """CLAUDE.md's reference schema says `block_name`; the real unit is a
        locality (see MEMORY.md "Flagged for review" #16). The API must not
        drift back to the doc's name, or a client built on the doc breaks.
        """
        assert "locality_name" in EvacuationPriority.model_fields
        assert "block_name" not in EvacuationPriority.model_fields

    def test_advisory_has_exactly_the_contract_fields(self):
        assert set(DistrictAdvisory.model_fields) == {
            "executive_summary",
            "evacuation_plan",
            "sms_dispatch_draft",
            "post_landfall_risks",
            "historical_context",
        }

    def test_model_is_pinned(self, payloads):
        """Rules.md: pin the model string, don't silently swap versions.

        The pin is a tripwire: changing the model without deciding to (and
        recording it in MEMORY.md #19) is supposed to fail here. It failed on
        2026-09-28 for exactly that reason, which is how the swap was confirmed
        deliberate rather than accidental.
        """
        assert advisory.ADVISORY_MODEL == "gemini-3.8-flash"

    def test_priority_level_is_a_closed_enum_not_a_bare_string(self):
        """The first live run returned "Immediate" three times and nothing
        caught it, because the four levels were only ever a comment. Now the
        schema itself rejects anything else."""
        field = EvacuationPriority.model_fields["priority_level"]
        assert set(literal for literal in field.annotation.__args__) == {
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
        }
        for bad in ("Immediate", "Urgent", "critical", "HIGHEST"):
            with pytest.raises(ValidationError):
                EvacuationPriority(locality_name="X", priority_level=bad, reasoning="r")

    def test_system_prompt_names_the_four_levels(self):
        """A Literal constrains the decoder; the prompt is what makes the model
        actually use them, and it used to say nothing at all."""
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            assert level in advisory.SYSTEM_PROMPT
        assert "Immediate" in advisory.SYSTEM_PROMPT, "should name what not to use"


class TestPrompt:
    def test_uses_the_modelled_area_not_the_drawn_area(self, payloads):
        """The two figures differ substantially at peak. Prompting on the drawn
        one would have the model understate the flood.

        The negative half of this assertion used to be a bare
        `str(drawn) not in prompt`, which only worked because the drawn figure
        happened to be a long distinctive decimal. At the anchored-scaling top
        band the drawn area is 0.0, and "0.0" occurs in the prompt for
        unrelated reasons ("120.0 kmph"), so the substring test passed/failed
        on a coincidence. It now checks the drawn figure is not the one
        *labelled as the flooded area*, which is the actual requirement and
        cannot be satisfied by an unrelated number elsewhere in the text.
        """
        surge = payloads["surge"]
        prompt = build_prompt(
            payloads["surge"], payloads["exposure"], payloads["allocation"]
        )
        assert f"FLOODED AREA: {surge['final_land_area_km2']} km2" in prompt
        assert f"FLOODED AREA: {surge['drawn_area_km2']} km2" not in prompt

    def test_labels_infrastructure_counts_as_district_wide(self, payloads):
        """Exposure has no per-locality breakdown; the prompt must not imply one."""
        prompt = build_prompt(
            payloads["surge"], payloads["exposure"], payloads["allocation"]
        )
        assert "HOSPITALS AFFECTED (district-wide)" in prompt
        assert "SUBSTATIONS AFFECTED (district-wide)" in prompt

    def test_passes_the_shelter_disclosure_through(self, payloads):
        """The capacities are derived, not surveyed. That has to reach Gemini."""
        prompt = build_prompt(
            payloads["surge"], payloads["exposure"], payloads["allocation"]
        )
        assert "CAPACITY BASIS" in prompt
        assert "DERIVED" in prompt

    def test_the_prompt_delimits_the_origin_block(self, payloads):
        """`context` arrives as pre-rendered prose about the requesting
        locality; without a `===` header it reads as more district-wide
        numbers, and the two scopes mix."""
        context = "REQUESTING LOCALITY: Kakdwip\nIT has a flood-free route."
        prompt = build_prompt(
            payloads["surge"], payloads["exposure"], payloads["allocation"],
            context=context,
        )
        header = prompt.index("=== REQUESTING LOCALITY ===")
        body = prompt.index("REQUESTING LOCALITY: Kakdwip")
        counts = prompt.index("HOSPITALS AFFECTED")
        assert counts < header < body

    def test_the_prompt_labels_the_district_figures_block(self, payloads):
        prompt = build_prompt(
            payloads["surge"], payloads["exposure"], payloads["allocation"]
        )
        header = prompt.find("=== COMPUTED FIGURES ===")
        counts = prompt.index("HOSPITALS AFFECTED")
        assert header != -1
        assert header < counts

    def test_corrections_are_appended_only_when_supplied(self, payloads):
        args = (payloads["surge"], payloads["exposure"], payloads["allocation"])
        assert "failed these checks" not in build_prompt(*args)
        assert "- sms_dispatch_draft is 200 chars" in build_prompt(
            *args, corrections="- sms_dispatch_draft is 200 chars"
        )

    def test_system_prompt_binds_the_honesty_rules(self):
        for rule in (
            "provisional",
            "final_land_area_km2",
            "never invent a locality",
            "DISTRICT-WIDE",
            "160 characters",
        ):
            assert rule.lower() in advisory.SYSTEM_PROMPT.lower(), rule

    def test_system_prompt_requires_every_allocation_locality(self):
        """Rule 9, and it is a separate rule from the no-invention one.

        The two pull in opposite directions and the model needs both stated
        plainly: rule 3 stops it naming a place that is not in the data, rule 9
        stops it dropping a place that is. Folding them together is what let
        7 of 12 localities through with no priority at all on the first live
        run — the instruction was there, buried inside another rule."""
        prompt = advisory.SYSTEM_PROMPT
        assert "9." in prompt
        assert "EVERY locality listed in the allocation data" in prompt
        assert "no more and no fewer" in prompt

    def test_historical_pool_is_a_closed_list(self):
        """Rules.md: every historical number traceable to a named source."""
        assert "Amphan" in advisory.VERIFIED_HISTORICAL_POOL
        assert "Yaas" in advisory.VERIFIED_HISTORICAL_POOL
        assert "Bulbul" in advisory.VERIFIED_HISTORICAL_POOL


# --------------------------------------------------------------------------
# The validator — the honesty rules, exercised directly
# --------------------------------------------------------------------------


class TestValidator:
    @pytest.fixture
    def known(self, payloads_with_allocation):
        return [row["node"] for row in payloads_with_allocation["allocation"]["allocation"]]

    def test_accepts_a_compliant_advisory(self, payloads_with_allocation, known):
        assert validate_advisory(a_valid_advisory(known), payloads_with_allocation["allocation"]) == []

    def test_catches_an_oversized_sms(self, payloads_with_allocation, known):
        draft = a_valid_advisory(known)
        draft.sms_dispatch_draft = "x" * 200
        violations = validate_advisory(draft, payloads_with_allocation["allocation"])
        assert any("sms_dispatch_draft" in v and "200" in v for v in violations)

    def test_catches_an_invented_locality(self, payloads_with_allocation, known):
        draft = a_valid_advisory(known)
        draft.evacuation_plan.append(
            EvacuationPriority(
                locality_name="Atlantis Nagar",
                priority_level="CRITICAL",
                reasoning="Seems bad.",
            )
        )
        violations = validate_advisory(draft, payloads_with_allocation["allocation"])
        assert any("Atlantis Nagar" in v for v in violations)

    def test_catches_missing_shelter_disclosure(self, payloads_with_allocation, known):
        """The API's shelters are placeholders. A draft that never says so
        reads as a real facility count — the exact misreading §13 warns about.
        """
        assert payloads_with_allocation["allocation"]["shelter_status"]["is_demo_data"] is True
        draft = a_valid_advisory(known)
        draft.executive_summary = "Evacuate the low-lying delta immediately."
        draft.sms_dispatch_draft = "Cyclone alert. Evacuate low-lying areas now."
        violations = validate_advisory(draft, payloads_with_allocation["allocation"])
        assert any("disclosure" in v for v in violations)

    def test_disclosure_in_either_field_counts(self, payloads_with_allocation, known):
        draft = a_valid_advisory(known)
        draft.sms_dispatch_draft = "Cyclone alert: shelter capacities are provisional. Evacuate now."
        assert validate_advisory(draft, payloads_with_allocation["allocation"]) == []

    def test_sms_boundary_is_measured_not_trusted(self, payloads_with_allocation, known):
        """159 passes, 160 fails. The rule is 'under 160', and it is checked by
        len() on the real string, not by believing the prompt was obeyed."""
        draft = a_valid_advisory(known)
        draft.sms_dispatch_draft = "y" * 159
        assert validate_advisory(draft, payloads_with_allocation["allocation"]) == []
        draft.sms_dispatch_draft = "y" * 160
        assert validate_advisory(draft, payloads_with_allocation["allocation"])


# --------------------------------------------------------------------------
# The handler — failures, retries, and the "no key" path
# --------------------------------------------------------------------------


class TestHandlerFailures:
    def test_missing_key_is_503_with_an_actionable_message(self, client, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "GEMINI_API_KEY" in detail
        assert "not a tracked file" in detail or "tracked file" in detail

    def test_missing_key_never_reaches_gemini(self, client, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("Gemini was called with no key configured")

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(main, "generate_advisory", explode)
        assert client.post("/advisory?category=6&origin=kakdwip").status_code == 503

    def test_key_is_read_at_request_time(self, client, monkeypatch):
        """Not import time — a key added after startup must take effect, and
        this is what makes the whole 503 path testable at all."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        calls = []

        def fake(surge, exposure, allocation, context="", corrections=""):
            calls.append(1)
            return a_valid_advisory([r["node"] for r in allocation["allocation"]])

        monkeypatch.setattr(main, "generate_advisory", fake)
        assert client.post("/advisory?category=6&origin=kakdwip").status_code == 200
        assert calls

    def test_a_gemini_exception_is_502_not_a_500(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

        def boom(*a, **k):
            raise RuntimeError("quota exhausted")

        monkeypatch.setattr(main, "generate_advisory", boom)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 502
        assert "quota exhausted" in response.json()["detail"]

    def test_unfixable_output_is_withheld_with_its_violations(
        self, client, monkeypatch
    ):
        """Two failed attempts means the prose is inventing things. Returning it
        with a disclaimer would still be shipping the invention."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

        def always_invents(surge, exposure, allocation, context="", corrections=""):
            draft = a_valid_advisory([r["node"] for r in allocation["allocation"]])
            draft.evacuation_plan.append(
                EvacuationPriority(
                    locality_name="Atlantis Nagar",
                    priority_level="CRITICAL",
                    reasoning="Invented.",
                )
            )
            return draft

        monkeypatch.setattr(main, "generate_advisory", always_invents)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "Atlantis Nagar" in detail["violations"][0]
        assert "withheld" in detail["message"]


class TestOriginAlwaysPresent:
    """The requesting locality must be in evacuation_plan, every time.

    The first live run asked for `sagar` and got an advisory that never
    mentioned Sagar — not in the plan, not in the summary — while the prompt
    context we sent it said "IT is UNREACHABLE at this intensity". Sagar had no
    population estimate back then, so it was not in the allocation locality
    list, and system-prompt rule 3 told the model to use only those. The entry
    is now built in code, from the same routing facts the prompt was given.

    **These tests arrange their own premise.** The guarantee is "if the model
    omitted the origin, code adds it", so the stub has to omit it — the `_stub`
    fixture below does that for whichever origin the test requests. They used
    to get that for free, because a plan built from the allocation's node names
    could not name a locality that had no allocation row. That stopped being
    true when the category unit fix landed: at category 6 the allocation has 21
    rows and Sagar is one of them, so the free version of the arrangement
    quietly stopped exercising the code path, and five of these tests went red
    without the product changing at all. Every one of them asserts on
    `evacuation_plan[0]` being the code-built entry, and `a_valid_advisory`'s
    generic per-locality text ("Estimated exposed population...") had taken
    that slot instead. Arranging the omission explicitly is what the test
    means, so it is now stated rather than inherited.
    """

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        # Deliberately returns an advisory that mentions the origin nowhere —
        # the exact failure being guarded against. `self._omit` is set by the
        # test that needs the arrangement, so it is stated at the call site
        # rather than inherited from whichever rows the allocation happens to
        # hold. A test that does not set it gets the full plan, which is the
        # "the model did mention it" case.
        def generate(surge, exposure, allocation, context="", corrections=""):
            return a_valid_advisory(
                [r["node"] for r in allocation["allocation"]], omit=self._omit
            )

        monkeypatch.setattr(main, "generate_advisory", generate)

    def _omit_origin(self, name: str) -> None:
        """Arrange for the stubbed model to leave `name` out of its plan."""
        self._omit = name

    _omit = None

    def test_origin_missing_from_allocation_is_still_added(self, client):
        self._omit_origin("Sagar")
        body = client.post("/advisory?category=6&origin=sagar").json()
        plan = body["advisory"]["evacuation_plan"]
        assert plan[0]["locality_name"] == "Sagar", "must lead the plan"
        assert body["generated_for"]["origin_entry_added_in_code"] is True

    def test_origin_status_is_sourced_from_routing_not_invented(self, client):
        self._omit_origin("Sagar")
        body = client.post("/advisory?category=6&origin=sagar").json()
        entry = body["advisory"]["evacuation_plan"][0]
        assert entry["priority_level"] == "CRITICAL", "unreachable is the top priority"
        # The reasoning must match what the router actually said, not the model's
        # own account of the situation.
        assert body["generated_for"]["origin_reachable"] is False
        assert "UNREACHABLE" in entry["reasoning"]
        assert "not flooding" in entry["reasoning"] or "road-data" in entry["reasoning"]

    def test_no_population_figure_is_invented_for_the_origin(self, client):
        """A locality with no population estimate must not be given one.

        Anantapur, not Sagar. This used to assert against Sagar, back when
        Sagar had no allocation row; the category unit fix gave it one (344
        people at category 6, assigned to a shelter 25 km away), so Sagar now
        has a real figure and the note it was checking for is correctly absent.
        The guarantee is about a *missing* estimate, so it needs a locality
        that still has one missing at every intensity.
        """
        self._omit_origin("Anantapur")
        body = client.post("/advisory?category=3&origin=anantapur").json()
        assert body["generated_for"]["origin_in_allocation"] is False
        entry = body["advisory"]["evacuation_plan"][0]
        assert entry["locality_name"] == "Anantapur"
        assert "no at-risk population estimate" in entry["reasoning"]
        # The route distance is legitimately quoted; a *people* count is not.
        # Look for one specifically rather than for any digit, or the check
        # trips over "155.1 km" and stops testing anything.
        assert not re.search(
            r"\d[\d.,]*\s*(?:people|persons|residents|inhabitants|"
            r"to be evacuated|evacuees)",
            entry["reasoning"],
            re.IGNORECASE,
        ), entry["reasoning"]

    def test_a_reachable_origin_is_added_as_high_not_critical(self, client):
        """Anantapur has no allocation row at any intensity, so the code-built
        entry is what puts it in the plan. Unreachable is the only condition
        that earns CRITICAL — checked against a *reachable* origin, at a
        category where it is still routable."""
        # Category 3 is the anchor band and floods nothing on this grid, so
        # Anantapur is reachable there and the HIGH/CRITICAL distinction is the
        # only thing under test. At category 6 the whole delta is cut off and
        # the router correctly reports it unreachable — which would make this
        # test assert nothing.
        body = client.post("/advisory?category=3&origin=anantapur").json()
        assert body["generated_for"]["origin_reachable"] is True
        assert body["generated_for"]["origin_in_allocation"] is False
        assert body["generated_for"]["origin_entry_added_in_code"] is True
        entry = body["advisory"]["evacuation_plan"][0]
        assert entry["locality_name"] == "Anantapur"
        assert entry["priority_level"] == "HIGH"
        assert "flood-free route" in entry["reasoning"]

    def test_an_origin_the_model_did_mention_is_not_duplicated(self, client):
        """Code-built must mean 'if absent', not 'always prepend'. The stub's
        plan names every allocation locality, and Kakdwip is one of them, so
        this is the inverse arrangement of the rest of the class."""
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        names = [e["locality_name"] for e in body["advisory"]["evacuation_plan"]]
        assert names.count("Kakdwip") == 1, names
        assert body["generated_for"]["origin_entry_added_in_code"] is False

    def test_the_guarantee_survives_the_retry_path(self, client, monkeypatch):
        """A retry replaces the whole object, so the code-built entry has to be
        re-applied. Missing this is invisible until a retry actually fires."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

        def bad_first_pass(surge, exposure, allocation, context="", corrections=""):
            result = a_valid_advisory(
                [r["node"] for r in allocation["allocation"]], omit="Sagar"
            )
            if not corrections:
                result.sms_dispatch_draft = "z" * 400  # forces a retry
            return result

        monkeypatch.setattr(main, "generate_advisory", bad_first_pass)
        body = client.post("/advisory?category=6&origin=sagar").json()
        assert body["validation"]["attempts"] == 2
        assert body["advisory"]["evacuation_plan"][0]["locality_name"] == "Sagar"

    def test_origin_does_not_trip_the_invented_locality_check(self, client):
        """The two rules must not cancel: the origin is legitimately outside the
        allocation, and validate_advisory has to accept it."""
        body = client.post("/advisory?category=6&origin=sagar").json()
        result = DistrictAdvisory(**body["advisory"])
        assert validate_advisory(result, main.allocation(6), "Sagar") == []


class TestNoInventedEmergencyNumbers:
    """The first live run's SMS said "Dial 1077". West Bengal's cyclone
    helpline is 1070; 1077 appears in no official listing. A wrong emergency
    number in a copyable SMS is the most damaging thing this system could
    emit, and no schema can catch it — only a test can.
    """

    # A standalone 4-digit token: not part of a longer number, decimal or
    # thousands-separated figure. "1820.83" and "46,300" must not match.
    HELPLINE_SHAPED = re.compile(r"(?<![\d.,])\d{4}(?![\d.,])")

    def _numbers_in(self, text: str) -> set[str]:
        return set(self.HELPLINE_SHAPED.findall(text))

    def test_pool_contains_only_112(self):
        """3-digit, so the 4-digit scanner cannot see it — check it directly."""
        pool = advisory.VERIFIED_EMERGENCY_CONTACTS
        assert "112" in pool
        # Nothing resembling a short code beyond the one permitted entry.
        assert not re.search(r"(?<![\d.,])\d{3,}(?![\d.,])", pool.replace("112", ""))

    def test_system_prompt_forbids_inventing_a_number(self):
        # The prompt is hard-wrapped, so normalise whitespace before matching.
        flattened = " ".join(advisory.SYSTEM_PROMPT.lower().split())
        assert "never invent or recall a number" in flattened
        assert "verified contacts pool" in flattened

    def test_the_first_live_sms_violation_is_caught_by_this_check(self):
        """Guard the guard: the exact string that came back on 2026-09-28."""
        sms = (
            "EMERGENCY: Super Cyclone (185km/h, 3.86m surge). Evacuate to nearest "
            "reinforced concrete shelter now. Dial 1077 for WB disaster assistance."
        )
        found = self._numbers_in(sms)
        assert "1077" in found, "the regression this test exists for must be caught"
        assert found - {"112"}, "and it must not be excused"

    def test_legitimate_numbers_are_not_flagged(self):
        """A check that flags everything gets switched off, so it has to be
        shown to pass on real output."""
        clean = (
            "Amphan (2020) made landfall with 1820.83 km2 affected and 46,300 "
            "people at 142.0 kmph. Surge 3.86 m. Call 112."
        )
        assert self._numbers_in(clean) <= {"112", "2020"}

    @requires_key
    def test_no_unverified_number_reaches_a_live_advisory(self, client):
        """Live-only: the real check. Skipped without a key, and the stubbed
        tests above are what keep the pattern honest in its absence.

        The `@requires_key` here was missing until 2026-09-28 — the docstring
        claimed a skip the test did not have, so it only ever passed because a
        key happened to be in `.env`.
        """
        body = _advisory_body(client, "/advisory?category=6&origin=sagar")
        result = DistrictAdvisory(**body["advisory"])
        blob = " ".join(
            [
                result.executive_summary,
                result.sms_dispatch_draft,
                result.post_landfall_risks,
                result.historical_context,
                *[e["reasoning"] for e in result.evacuation_plan],
            ]
        )
        unverified = self._numbers_in(blob) - {"112"} - {
            str(y) for y in range(1900, 2100)  # years in historical_context
        }
        assert not unverified, f"unverified emergency/helpline numbers: {unverified}"


class TestRetry:
    def test_a_single_violation_triggers_one_correction_pass(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        seen: list[tuple[str, int]] = []

        def recovers(surge, exposure, allocation, context="", corrections=""):
            names = [r["node"] for r in allocation["allocation"]]
            draft = a_valid_advisory(names)
            if not corrections:
                # First pass: too long, as models sometimes are.
                draft.sms_dispatch_draft = "z" * 240
            else:
                seen.append(("recovered", 1))
            return draft

        monkeypatch.setattr(main, "generate_advisory", recovers)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 200
        body = response.json()
        assert body["validation"]["attempts"] == 2
        assert seen == [("recovered", 1)]

    def test_a_compliant_first_pass_is_not_retried(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        calls = []

        def fine(surge, exposure, allocation, context="", corrections=""):
            calls.append(corrections)
            return a_valid_advisory([r["node"] for r in allocation["allocation"]])

        monkeypatch.setattr(main, "generate_advisory", fine)
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        assert body["validation"]["attempts"] == 1
        assert calls == [""]

    def test_retries_are_capped_at_one(self, client, monkeypatch):
        """The free tier is rate-limited; a retry loop would be a bug."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        calls = []

        def bad(surge, exposure, allocation, context="", corrections=""):
            calls.append(1)
            draft = a_valid_advisory([r["node"] for r in allocation["allocation"]])
            draft.sms_dispatch_draft = "q" * 400
            return draft

        monkeypatch.setattr(main, "generate_advisory", bad)
        assert client.post("/advisory?category=6&origin=kakdwip").status_code == 502
        assert len(calls) == 2


class TestHandlerPayload:
    @pytest.fixture(autouse=True)
    def _stub_gemini(self, client, monkeypatch):
        """Stub Gemini and record exactly what the handler handed it."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        captured: dict = {}
        _CAPTURED.clear()
        _CAPTURED.update(captured)

        def fake(surge, exposure, allocation, context="", corrections=""):
            _CAPTURED.clear()
            _CAPTURED.update(
                {
                    "surge": surge,
                    "exposure": exposure,
                    "allocation": allocation,
                    "context": context,
                    "corrections": corrections,
                }
            )
            return a_valid_advisory([r["node"] for r in allocation["allocation"]])

        monkeypatch.setattr(main, "generate_advisory", fake)

    def test_sends_the_real_endpoint_payloads(self, client):
        """Not re-derived numbers. The advisory and the map must agree because
        they are the same dicts."""
        client.post("/advisory?category=6&origin=kakdwip")
        assert _CAPTURED["surge"]["final_land_area_km2"] == main.surge_zone(6)[
            "final_land_area_km2"
        ]
        assert _CAPTURED["exposure"]["hospitals"]["count"] == main.exposure(6)["hospitals"][
            "count"
        ]
        assert _CAPTURED["allocation"]["allocation"] == main.allocation(6)["allocation"]

    def test_carries_the_disclosure_fields_into_the_prompt(self, client):
        client.post("/advisory?category=6&origin=kakdwip")
        allocation = _CAPTURED["allocation"]
        assert allocation["capacity_basis"]["shelters_are_real"] is False
        assert allocation["shelter_status"]["is_demo_data"] is True

    def test_origin_reaches_the_prompt(self, client):
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        assert body["generated_for"]["origin"]["id"] == "kakdwip"
        assert "Kakdwip" in body["generated_for"]["origin_context"]
        assert "REQUESTING LOCALITY" in _CAPTURED["context"]

    def test_reports_which_model_wrote_it(self, client):
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        assert body["model"] == "gemini-3.8-flash"
        assert body["validated"] is True
        assert body["advisory"]["sms_dispatch_draft"]

    def test_the_returned_advisory_passed_validation(
        self, client, synthetic_allocation_result
    ):
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        result = DistrictAdvisory(**body["advisory"])
        assert validate_advisory(result, main.allocation(6)) == []


# --------------------------------------------------------------------------
# Capacity blocks — 503 UNAVAILABLE is a fact about the model, not a bug
# --------------------------------------------------------------------------


class TestCapacityRetry:
    """`gemini-3.8-flash` is intermittently 503 UNAVAILABLE.

    Observed 2026-09-28: a `POST /advisory` returned 502 on attempt 1 and 200
    on attempt 2 eight seconds later, and a read-only probe found
    `gemini-3.5-flash` failing 3/3 while 3.8 answered. So the fix is to wait the
    blip out on the SAME pinned model — Rules.md forbids a silent swap, and a
    swap would not have helped anyway, since 3.5 was the one that was blocked.
    """

    @pytest.fixture(autouse=True)
    def _no_real_sleep(self, monkeypatch):
        self.slept: list[float] = []
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setattr(main, "_sleep", self.slept.append)

    def test_two_capacity_failures_then_success_is_a_200(self, client, monkeypatch):
        calls: list[int] = []

        def flaky(surge, exposure, allocation, context="", corrections=""):
            calls.append(1)
            if len(calls) <= 2:
                raise capacity_error()
            return a_valid_advisory([r["node"] for r in allocation["allocation"]])

        monkeypatch.setattr(main, "generate_advisory", flaky)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 200, response.text
        assert len(calls) == 3, "should have taken the third attempt"

    def test_backoff_is_two_then_four(self, client, monkeypatch):
        """2s then 4s, and no third wait — the spec, measured rather than hoped.

        Three attempts leave two gaps. The 8s rung exists in the ladder but
        does not fire at this ceiling; the test pins the two that do, so a
        raised cap cannot silently reuse one of them."""

        def flaky(surge, exposure, allocation, context="", corrections=""):
            raise capacity_error()

        monkeypatch.setattr(main, "generate_advisory", flaky)
        client.post("/advisory?category=6&origin=kakdwip")
        assert self.slept == [2, 4], self.slept

    def test_the_backoff_ladder_is_2_4_8(self):
        """The full ladder, so raising the ceiling needs no new numbers."""
        assert main.CAPACITY_BACKOFF_SECONDS == (2, 4, 8)

    def test_three_attempts_is_the_ceiling(self, client, monkeypatch):
        """A busy model must not turn one button press into an unbounded loop."""
        calls: list[int] = []

        def always_busy(surge, exposure, allocation, context="", corrections=""):
            calls.append(1)
            raise capacity_error()

        monkeypatch.setattr(main, "generate_advisory", always_busy)
        assert client.post("/advisory?category=6&origin=kakdwip").status_code == 503
        assert len(calls) == 3, calls

    def test_exhaustion_reports_the_attempt_count(self, client, monkeypatch):
        """A client that sees one slow advisory and another slow advisory needs
        to tell "tried once" from "tried three times, the model stayed busy"."""
        monkeypatch.setattr(
            main,
            "generate_advisory",
            lambda *a, **k: (_ for _ in ()).throw(capacity_error()),
        )
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "60"
        assert "all 3 attempts" in response.json()["detail"]

    def test_the_reported_wait_is_measured_not_assumed(self, client, monkeypatch):
        """The message says how long it waited. Summing the whole ladder would
        claim 14s when only 2s + 4s ever elapsed."""
        monkeypatch.setattr(
            main,
            "generate_advisory",
            lambda *a, **k: (_ for _ in ()).throw(capacity_error()),
        )
        detail = client.post("/advisory?category=6&origin=kakdwip").json()["detail"]
        assert "over 6s" in detail, detail

    def test_gemini_calls_counts_the_retries(self, client, monkeypatch):
        """Two capacity blocks then success is three real calls to the API, and
        the response has to admit that."""
        calls: list[int] = []

        def flaky(surge, exposure, allocation, context="", corrections=""):
            calls.append(1)
            if len(calls) <= 2:
                raise capacity_error()
            return a_valid_advisory([r["node"] for r in allocation["allocation"]])

        monkeypatch.setattr(main, "generate_advisory", flaky)
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        assert body["validation"]["gemini_calls"] == 3
        # One pass, no correction: the two counts are different numbers, not
        # the same number written twice.
        assert body["validation"]["attempts"] == 1

    def test_gemini_calls_includes_the_correction_pass(self, client, monkeypatch):
        calls: list[int] = []

        def needs_correction(surge, exposure, allocation, context="", corrections=""):
            calls.append(1)
            names = [r["node"] for r in allocation["allocation"]]
            if corrections:
                return a_valid_advisory(names)
            draft = a_valid_advisory(names)
            draft.sms_dispatch_draft = "z" * 400
            return draft

        monkeypatch.setattr(main, "generate_advisory", needs_correction)
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        assert body["validation"]["gemini_calls"] == 2
        assert body["validation"]["attempts"] == 2

    def test_the_capacity_message_does_not_blame_the_code(self, client, monkeypatch):
        monkeypatch.setattr(
            main,
            "generate_advisory",
            lambda *a, **k: (_ for _ in ()).throw(capacity_error()),
        )
        detail = client.post("/advisory?category=6&origin=kakdwip").json()["detail"]
        assert "at capacity" in detail
        assert "not a fault in this service" in detail

    def test_a_non_capacity_server_error_is_not_retried(self, client, monkeypatch):
        """A 500 is ours to fix. Retrying it just spends quota on a bug."""
        calls: list[int] = []

        def broken(surge, exposure, allocation, context="", corrections=""):
            calls.append(1)
            raise ServerError(500, {"error": {"code": 500, "message": "INTERNAL"}})

        monkeypatch.setattr(main, "generate_advisory", broken)
        assert client.post("/advisory?category=6&origin=kakdwip").status_code == 502
        assert len(calls) == 1, calls

    def test_the_model_is_never_swapped_to_escape_a_block(self, monkeypatch):
        """Rules.md: pin the model, decide deliberately. Retry, don't rotate."""
        assert main.CAPACITY_MAX_ATTEMPTS == 3
        assert main.ADVISORY_MODEL == "gemini-3.8-flash"
        assert main._is_capacity_error(capacity_error()) is True

    def test_capacity_retry_covers_the_correction_pass_too(self, client, monkeypatch):
        """The first pass can succeed and the correction pass hit a block. The
        user still deserves the capacity story and a Retry-After, not a bare
        upstream error."""

        def busy_on_correction(surge, exposure, allocation, context="", corrections=""):
            if corrections:
                raise capacity_error()
            draft = a_valid_advisory([r["node"] for r in allocation["allocation"]])
            draft.sms_dispatch_draft = "z" * 400  # forces the correction pass
            return draft

        monkeypatch.setattr(main, "generate_advisory", busy_on_correction)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "60"
        assert self.slept == [2, 4]
        assert "at capacity" in response.json()["detail"]


# --------------------------------------------------------------------------
# Punctuation in the code-built origin entry
# --------------------------------------------------------------------------


class TestReasoningPunctuation:
    """§25: the /routes reason was spliced in without a stop, so the entry read
    "...road-data coverage, not flooding Evacuation cannot proceed...".
    """

    def test_a_missing_terminal_period_is_added(self):
        assert main._as_sentence("not flooding") == "not flooding."

    def test_an_existing_stop_is_kept_and_not_doubled(self):
        assert main._as_sentence("not flooding.") == "not flooding."
        for ending in ("!", "?"):
            assert main._as_sentence(f"really{ending}") == f"really{ending}"

    def test_stray_whitespace_is_collapsed(self):
        """The reason is stitched from several diagnostic fragments."""
        assert main._as_sentence("  a   b\n c  ") == "a b c."

    def test_empty_reason_does_not_become_a_stray_period(self):
        assert main._as_sentence("") == ""
        assert main._as_sentence(None) == ""

    def test_the_built_reasoning_reads_as_two_sentences(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        # Sagar is in the allocation at category 6, so the plan built from the
        # allocation's own names mentions it and the code-built entry — the one
        # that splices the /routes reason in — is never reached. Omit it.
        monkeypatch.setattr(
            main,
            "generate_advisory",
            lambda surge, exposure, allocation, context="", corrections="": (
                a_valid_advisory(
                    [r["node"] for r in allocation["allocation"]], omit="Sagar"
                )
            ),
        )
        body = client.post("/advisory?category=6&origin=sagar").json()
        entry = body["advisory"]["evacuation_plan"][0]
        reason = body["generated_for"]["origin_reason"]
        assert entry["locality_name"] == "Sagar", "must be the code-built entry"
        assert f"intensity: {reason}." in entry["reasoning"]
        # The specific run-on from the third live run.
        assert "not flooding Evacuation" not in entry["reasoning"]


# --------------------------------------------------------------------------
# Plan coverage — every allocation locality must get a priority
# --------------------------------------------------------------------------


class TestPlanCoverage:
    """§23: the model named 7 of 12 allocation localities and nothing noticed.

    A locality in the allocation is a place with a computed at-risk population
    and a shelter assignment. Leaving it out of the plan means the advisory
    never tells anyone there to leave, and it validated clean.
    """

    @pytest.fixture
    def known(self, payloads_with_allocation):
        return [row["node"] for row in payloads_with_allocation["allocation"]["allocation"]]

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch):
        """These are stubbed tests, so they must not depend on a real key being
        present — otherwise they pass or fail on the machine's `.env`."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    def test_a_full_plan_is_not_flagged(self, payloads_with_allocation, known):
        assert plan_coverage(a_valid_advisory(known), payloads_with_allocation["allocation"])[
            "label"
        ] == f"{len(known)}/{len(known)}"
        assert validate_advisory(a_valid_advisory(known), payloads_with_allocation["allocation"]) == []

    def test_a_partial_plan_is_a_violation(self, payloads_with_allocation, known):
        """The shape the live run produced: a plan covering some of the
        allocation is a violation, not a shorter but acceptable plan.

        The counts are derived from the allocation rather than hardcoded, so
        the test keeps testing the rule when the allocation's size changes —
        the original pinned the literal "covers only 7/" against a 12-locality
        real allocation, which meant it could only ever pass or fail on
        whether that specific allocation happened to exist.
        """
        partial = known[:-1]
        draft = a_valid_advisory(partial)
        violations = validate_advisory(draft, payloads_with_allocation["allocation"])
        assert any(
            f"covers only {len(partial)}/{len(known)}" in v for v in violations
        ), violations

    def test_the_violation_names_every_missing_locality(
        self, payloads_with_allocation, known
    ):
        """Because that string is what the correction pass feeds back to the
        model — a bare count gives it nothing to act on."""
        partial = known[:-1]
        draft = a_valid_advisory(partial)
        violation = next(
            v
            for v in validate_advisory(draft, payloads_with_allocation["allocation"])
            if "Missing" in v
        )
        for name in known[len(partial) :]:
            assert name in violation, name

    def test_coverage_counts_allocation_localities_not_plan_entries(
        self, payloads_with_allocation, known
    ):
        """The code-built origin entry is not an allocation locality, so a
        complete plan reads N/N rather than being inflated to N+1/N."""
        result = a_valid_advisory(known)
        result.evacuation_plan.insert(
            0,
            EvacuationPriority(
                locality_name="Sagar", priority_level="CRITICAL", reasoning="r"
            ),
        )
        assert plan_coverage(result, payloads_with_allocation["allocation"])["label"] == (
            f"{len(known)}/{len(known)}"
        )
        assert validate_advisory(result, payloads_with_allocation["allocation"], "Sagar") == []

    def test_missing_priorities_are_not_invented_in_code(
        self, client, monkeypatch, synthetic_allocation_result
    ):
        """The instruction is explicit: a priority is a judgement, so a
        half-covered plan must fail, not be quietly padded by a guess."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        seen: list[int] = []

        def half_covers(surge, exposure, allocation, context="", corrections=""):
            seen.append(1)
            names = [r["node"] for r in allocation["allocation"]]
            return a_valid_advisory(names[:-1])

        monkeypatch.setattr(main, "generate_advisory", half_covers)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 502
        violations = response.json()["detail"]["violations"]
        # Counts are not hardcoded: the code-built origin entry covers Kakdwip,
        # so the covered number is one higher than the plan the stub emitted.
        assert any("covers only" in v for v in violations), violations
        assert any("Missing:" in v for v in violations), violations
        # Both passes tried, and neither invented the missing priorities.
        assert len(seen) == 2

    def test_the_correction_pass_is_told_which_names_are_missing(
        self, client, monkeypatch, synthetic_allocation_result
    ):
        corrections_seen: list[str] = []

        def half_then_full(surge, exposure, allocation, context="", corrections=""):
            names = [r["node"] for r in allocation["allocation"]]
            if not corrections:
                return a_valid_advisory(names[:-1])
            corrections_seen.append(corrections)
            return a_valid_advisory(names)

        monkeypatch.setattr(main, "generate_advisory", half_then_full)
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        assert body["validation"]["attempts"] == 2
        # The stub drops the LAST allocation locality, so that is the one the
        # correction string has to name.
        all_names = [r["node"] for r in main.allocation(6)["allocation"]]
        missing = all_names[-1:]
        # Kakdwip is not among them: the origin entry is code-built, so the
        # model was never asked to supply it and must not be told it is missing.
        assert "Kakdwip" not in corrections_seen[0]
        for name in missing:
            if name == "Kakdwip":
                continue
            assert name in corrections_seen[0], name

    def test_coverage_is_reported_in_the_response_metadata(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setattr(
            main,
            "generate_advisory",
            lambda surge, exposure, allocation, context="", corrections="": (
                a_valid_advisory([r["node"] for r in allocation["allocation"]])
            ),
        )
        total = len(main.allocation(6)["allocation"])
        body = client.post("/advisory?category=6&origin=sagar").json()
        assert body["validation"]["plan_coverage"] == f"{total}/{total}"

    def test_coverage_is_among_the_stated_checks(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setattr(
            main,
            "generate_advisory",
            lambda surge, exposure, allocation, context="", corrections="": (
                a_valid_advisory([r["node"] for r in allocation["allocation"]])
            ),
        )
        checks = client.post("/advisory?category=6&origin=kakdwip").json()["validation"][
            "checks"
        ]
        assert any("allocation locality appears in evacuation_plan" in c for c in checks)


# --------------------------------------------------------------------------
# Out-of-district localities must not reach the prompt
# --------------------------------------------------------------------------


class TestOutOfDistrictExcluded:
    """§17/#24: Tamluk is in Purba Medinipur, not a 24 Parganas district."""

    def test_the_prompt_never_names_an_out_of_district_place(
        self, payloads_with_allocation
    ):
        """The prompt is what the model writes from. A place that reaches here
        can end up in a real person's SMS as a place to evacuate."""
        prompt = build_prompt(
            payloads_with_allocation["surge"],
            payloads_with_allocation["exposure"],
            payloads_with_allocation["allocation"],
        )
        assert "Tamluk" not in prompt

    def test_the_prompt_names_exactly_the_allocation_localities(
        self, payloads_with_allocation
    ):
        prompt = build_prompt(
            payloads_with_allocation["surge"],
            payloads_with_allocation["exposure"],
            payloads_with_allocation["allocation"],
        )
        listed = next(
            line for line in prompt.splitlines() if line.startswith("LOCALITIES IN THIS")
        )
        assert "Tamluk" not in listed
        assert "Kakdwip" in listed


# --------------------------------------------------------------------------
# Live calls — skipped without a key
# --------------------------------------------------------------------------


@requires_key
class TestLiveGemini:
    """The only tests that spend a request. One call, several assertions."""

    def test_generated_advisory_satisfies_every_rule(self, client):
        body = _advisory_body(client, "/advisory?category=6&origin=kakdwip")
        result = DistrictAdvisory(**body["advisory"])

        # The three rules the validator enforces, re-asserted at the boundary
        # rather than trusting the handler to have done it.
        assert len(result.sms_dispatch_draft) < 160, result.sms_dispatch_draft
        assert validate_advisory(result, main.allocation(6)) == []

        known = {row["node"] for row in main.allocation(6)["allocation"]}
        for item in result.evacuation_plan:
            assert item.locality_name in known, item.locality_name

        # Rules the validator does not mechanically check but that would still
        # be a shipped lie if broken.
        joined = (result.executive_summary + result.post_landfall_risks).lower()
        assert any(w in joined for w in ("provisional", "placeholder", "not surveyed"))
        assert any(
            storm in result.historical_context
            for storm in ("Amphan", "Yaas", "Bulbul")
        ), result.historical_context

    def test_sms_draft_is_short_enough_to_be_an_sms(self, client):
        body = _advisory_body(client, "/advisory?category=6&origin=kakdwip")
        draft = body["advisory"]["sms_dispatch_draft"]
        assert 0 < len(draft) < 160
        # 160 characters is two GSM-7 SMS segments. One is 160, so a draft that
        # needs the limit exceeded is two texts and a different product.
        assert len(draft) < 160
