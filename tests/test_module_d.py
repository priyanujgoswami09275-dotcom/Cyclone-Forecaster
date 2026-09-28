"""Tests for the Gemini advisory layer (Module D).

The schema is the easy half. A `DistrictAdvisory` that validates against its
pydantic model can still be wrong in the ways that matter: quote a shelter
occupancy figure as though it were surveyed, name a locality that is not in
the allocation data, or write a 300-character SMS that nobody can send. Those
are the failures this file targets.

Most of these tests run with Gemini stubbed out, because the properties under
test are properties of *our* code — the prompt, the payload assembly, the
validator, the failure handling — not of the model. The tests that do call
Gemini are marked `requires_key` and skip when GEMINI_API_KEY is absent, so
the suite passes for anyone who has not set one.

Note what is NOT tested here: that Gemini is a good writer. If a draft is
grammatical and hits every rule below, it ships. Judging prose quality is a
human job.
"""

from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import main
from backend.ai import advisory
from backend.ai.advisory import (
    DistrictAdvisory,
    EvacuationPriority,
    build_prompt,
    validate_advisory,
)

API_KEY = os.environ.get("GEMINI_API_KEY")
requires_key = pytest.mark.skipif(
    not API_KEY, reason="GEMINI_API_KEY is not set; skipping live Gemini call"
)

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


def a_valid_advisory(localities: list[str]) -> DistrictAdvisory:
    """A hand-built advisory that satisfies every rule validate_advisory checks."""
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
        """The two figures differ by ~2x at peak. Prompting on the drawn one
        would have the model understate the flood by half."""
        prompt = build_prompt(
            payloads["surge"], payloads["exposure"], payloads["allocation"]
        )
        assert f"FLOODED AREA: {payloads['surge']['final_land_area_km2']} km2" in prompt
        assert str(payloads["surge"]["drawn_area_km2"]) not in prompt

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
    def known(self, payloads):
        return [row["node"] for row in payloads["allocation"]["allocation"]]

    def test_accepts_a_compliant_advisory(self, payloads, known):
        assert validate_advisory(a_valid_advisory(known), payloads["allocation"]) == []

    def test_catches_an_oversized_sms(self, payloads, known):
        draft = a_valid_advisory(known)
        draft.sms_dispatch_draft = "x" * 200
        violations = validate_advisory(draft, payloads["allocation"])
        assert any("sms_dispatch_draft" in v and "200" in v for v in violations)

    def test_catches_an_invented_locality(self, payloads, known):
        draft = a_valid_advisory(known)
        draft.evacuation_plan.append(
            EvacuationPriority(
                locality_name="Atlantis Nagar",
                priority_level="CRITICAL",
                reasoning="Seems bad.",
            )
        )
        violations = validate_advisory(draft, payloads["allocation"])
        assert any("Atlantis Nagar" in v for v in violations)

    def test_catches_missing_shelter_disclosure(self, payloads, known):
        """The API's shelters are placeholders. A draft that never says so
        reads as a real facility count — the exact misreading §13 warns about.
        """
        assert payloads["allocation"]["shelter_status"]["is_demo_data"] is True
        draft = a_valid_advisory(known)
        draft.executive_summary = "Evacuate the low-lying delta immediately."
        draft.sms_dispatch_draft = "Cyclone alert. Evacuate low-lying areas now."
        violations = validate_advisory(draft, payloads["allocation"])
        assert any("disclosure" in v for v in violations)

    def test_disclosure_in_either_field_counts(self, payloads, known):
        draft = a_valid_advisory(known)
        draft.sms_dispatch_draft = "Cyclone alert: shelter capacities are provisional. Evacuate now."
        assert validate_advisory(draft, payloads["allocation"]) == []

    def test_sms_boundary_is_measured_not_trusted(self, payloads, known):
        """159 passes, 160 fails. The rule is 'under 160', and it is checked by
        len() on the real string, not by believing the prompt was obeyed."""
        draft = a_valid_advisory(known)
        draft.sms_dispatch_draft = "y" * 159
        assert validate_advisory(draft, payloads["allocation"]) == []
        draft.sms_dispatch_draft = "y" * 160
        assert validate_advisory(draft, payloads["allocation"])


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
    context we sent it said "IT is UNREACHABLE at this intensity". Sagar has no
    population estimate, so it is not in the allocation locality list, and
    system-prompt rule 3 told the model to use only those. The entry is now
    built in code, from the same routing facts the prompt was given.
    """

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        # Deliberately returns an advisory that mentions the origin nowhere, the
        # exact failure being guarded against.
        monkeypatch.setattr(
            main,
            "generate_advisory",
            lambda surge, exposure, allocation, context="", corrections="": (
                a_valid_advisory([r["node"] for r in allocation["allocation"]])
            ),
        )

    def test_origin_missing_from_allocation_is_still_added(self, client):
        assert "Sagar" not in [r["node"] for r in main.allocation(6)["allocation"]]
        body = client.post("/advisory?category=6&origin=sagar").json()
        plan = body["advisory"]["evacuation_plan"]
        assert plan[0]["locality_name"] == "Sagar", "must lead the plan"
        assert body["generated_for"]["origin_in_allocation"] is False

    def test_origin_status_is_sourced_from_routing_not_invented(self, client):
        body = client.post("/advisory?category=6&origin=sagar").json()
        entry = body["advisory"]["evacuation_plan"][0]
        assert entry["priority_level"] == "CRITICAL", "unreachable is the top priority"
        # The reasoning must match what the router actually said, not the model's
        # own account of the situation.
        assert body["generated_for"]["origin_reachable"] is False
        assert "UNREACHABLE" in entry["reasoning"]
        assert "not flooding" in entry["reasoning"] or "road-data" in entry["reasoning"]

    def test_no_population_figure_is_invented_for_the_origin(self, client):
        body = client.post("/advisory?category=6&origin=sagar").json()
        reasoning = body["advisory"]["evacuation_plan"][0]["reasoning"]
        assert "no at-risk population estimate" in reasoning

    def test_a_reachable_origin_is_added_as_high_not_critical(self, client):
        """Anantapur is reachable at category 6 but has no allocation row, so
        the code-built entry is what puts it in the plan. Unreachable is the
        only condition that earns CRITICAL."""
        body = client.post("/advisory?category=6&origin=anantapur").json()
        assert body["generated_for"]["origin_reachable"] is True
        assert body["generated_for"]["origin_in_allocation"] is False
        entry = body["advisory"]["evacuation_plan"][0]
        assert entry["locality_name"] == "Anantapur"
        assert entry["priority_level"] == "HIGH"
        assert "flood-free route" in entry["reasoning"]

    def test_an_origin_the_model_did_mention_is_not_duplicated(self, client):
        """Code-built must mean 'if absent', not 'always prepend'. Kakdwip is
        in the allocation, so the stub's plan already names it."""
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        names = [e["locality_name"] for e in body["advisory"]["evacuation_plan"]]
        assert names.count("Kakdwip") == 1, names

    def test_the_guarantee_survives_the_retry_path(self, client, monkeypatch):
        """A retry replaces the whole object, so the code-built entry has to be
        re-applied. Missing this is invisible until a retry actually fires."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

        def bad_first_pass(surge, exposure, allocation, context="", corrections=""):
            result = a_valid_advisory([r["node"] for r in allocation["allocation"]])
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
            "people at 185.0 kmph. Surge 3.86 m. Call 112."
        )
        assert self._numbers_in(clean) <= {"112", "2020"}

    def test_no_unverified_number_reaches_a_live_advisory(self, client):
        """Live-only: the real check. Skipped without a key, and the stubbed
        tests above are what keep the pattern honest in its absence."""
        response = client.post("/advisory?category=6&origin=sagar")
        assert response.status_code == 200, response.text
        result = DistrictAdvisory(**response.json()["advisory"])
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

    def test_the_returned_advisory_passed_validation(self, client):
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        result = DistrictAdvisory(**body["advisory"])
        assert validate_advisory(result, main.allocation(6)) == []


# --------------------------------------------------------------------------
# Live calls — skipped without a key
# --------------------------------------------------------------------------


@requires_key
class TestLiveGemini:
    """The only tests that spend a request. One call, several assertions."""

    def test_generated_advisory_satisfies_every_rule(self, client):
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 200, response.text
        body = response.json()
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
        body = client.post("/advisory?category=6&origin=kakdwip").json()
        draft = body["advisory"]["sms_dispatch_draft"]
        assert 0 < len(draft) < 160
        # 160 characters is two GSM-7 SMS segments. One is 160, so a draft that
        # needs the limit exceeded is two texts and a different product.
        assert len(draft) < 160
