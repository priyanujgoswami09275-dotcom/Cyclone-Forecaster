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

import pytest
from fastapi.testclient import TestClient

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
