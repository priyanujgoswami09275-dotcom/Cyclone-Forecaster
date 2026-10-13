"""Rule 8 in code: only 112 from the verified contacts pool is a permitted number.

SYSTEM_PROMPT already tells the model this; `validate_advisory` now enforces it.
These tests cover the exact cases the rule exists for — invented helplines,
recalled mobile numbers, plausible-sounding toll-free lines — and the figures
that must NOT be flagged (winds, areas, populations, years, counts).
"""

import pytest

from backend.ai.advisory import (
    DistrictAdvisory,
    EvacuationPriority,
    validate_advisory,
)


def allocation() -> dict:
    """The allocation shape validate_advisory expects."""
    return {
        "allocation": [
            {"node": "namkhana"},
            {"node": "kakdwip"},
        ],
        "shelter_status": {"is_demo_data": True},
    }


def advisory_with(text: str, field: str = "executive_summary") -> DistrictAdvisory:
    """A minimal advisory with `text` injected into one field."""
    kwargs = {
        "executive_summary": "Provisional advisory with placeholder shelter data.",
        "evacuation_plan": [
            EvacuationPriority(locality_name="namkhana", priority_level="HIGH", reasoning="Reachable."),
            EvacuationPriority(locality_name="kakdwip", priority_level="HIGH", reasoning="Reachable."),
        ],
        "sms_dispatch_draft": "Evacuate to provisional shelters. Placeholder data.",
        "post_landfall_risks": "Freshwater flooding and salinization expected.",
        "historical_context": "Comparable to Cyclone Amphan (2020).",
    }
    kwargs[field] = text
    return DistrictAdvisory(**kwargs)


class TestPhoneNumbersFlagged:
    """Each of these must produce a violation naming the offending number."""

    @pytest.mark.parametrize("text,expected", [
        ("Call 1070 for flood assistance.", "1070"),
        ("Dial 108 for ambulance.", "108"),
        ("Helpline 1800-345-1234 is available.", "1800"),
        ("Toll-free 1077 for help.", "1077"),
    ])
    def test_context_word_flagged(self, text, expected):
        violations = validate_advisory(advisory_with(text), allocation())
        phone_violations = [v for v in violations if "112" in v or "number" in v.lower()]
        assert any(expected in v for v in violations), (
            f"'{text}' must produce a violation naming {expected}; got: {violations}"
        )

    def test_ten_digit_mobile_flagged(self):
        text = "For help, call +91 98300 12345 immediately."
        violations = validate_advisory(advisory_with(text), allocation())
        assert any("98300" in v or "12345" in v for v in violations), (
            f"a 10-digit mobile number must be flagged; got: {violations}"
        )

    def test_bare_10_digit_flagged(self):
        text = "The officer's number is 9830012345."
        violations = validate_advisory(advisory_with(text), allocation())
        assert any("9830012345" in v for v in violations), (
            f"a bare 10-digit number must be flagged; got: {violations}"
        )

    def test_reasoning_field_checked(self):
        """The check must scan evacuation_plan reasoning, not just the summary."""
        adv = advisory_with("Safe text.")
        adv.evacuation_plan[0].reasoning = "Call 1070 for evacuation help."
        violations = validate_advisory(adv, allocation())
        assert any("1070" in v for v in violations), (
            f"a number in reasoning must be flagged; got: {violations}"
        )

    def test_sms_field_checked(self):
        violations = validate_advisory(
            advisory_with("Dial 108 for medical emergency.", field="sms_dispatch_draft"),
            allocation(),
        )
        assert any("108" in v for v in violations), (
            f"a number in sms_dispatch_draft must be flagged; got: {violations}"
        )


class TestOrdinaryFiguresAllowed:
    """None of these may produce a phone-number violation."""

    @pytest.mark.parametrize("text", [
        "Call 112 for all emergencies.",
        "The national emergency number is 112.",
        "Dial 112 for help.",
        "112",
        "Wind speeds of 194 kmph expected.",
        "Storm surge of 3.41 m projected.",
        "An estimated 129,000 people are at risk.",
        "Approximately 1,709 km2 of land flooded.",
        "Historical context: Cyclone Amphan (2020).",
        "The storm made landfall in 2020.",
        "12 localities are covered by this plan.",
        "122 road segments touched by the flood.",
        "5 hospitals and 10 substations reached.",
        # Tightened-rule additions: these were false positives under the
        # old 40-char/10-word rule and must not be flagged.
        "emergency shelters with capacity 1200",
        "the number of families is 3500",
        "contact district officials about 250 families",
        "estimated 4500 people need shelter",
        "emergency exposure of 1,709 km2",
        "phone and power outages affect 2200 homes",
    ])
    def test_no_phone_violation(self, text):
        violations = validate_advisory(advisory_with(text), allocation())
        # Filter to just phone-number violations (other checks may fire).
        phone = [v for v in violations if "verified contacts pool" in v]
        assert phone == [], (
            f"'{text}' must not produce a phone-number violation; got: {phone}"
        )


class TestTightenedMustFlag:
    """The tightened rule must still catch the real helpline numbers."""

    @pytest.mark.parametrize("text,expected", [
        ("call 1070, the state helpline", "1070"),
        ("call 1070.", "1070"),
        ("dial 108", "108"),
        ("helpline 1800-345-1234", "1800"),
        ("ring 1077 now", "1077"),
        ("+91 98300 12345", "98300"),
        ("toll-free 1070", "1070"),
        ("hotline: 1070", "1070"),
    ])
    def test_still_flagged(self, text, expected):
        violations = validate_advisory(advisory_with(text), allocation())
        assert any(expected in v for v in violations), (
            f"'{text}' must produce a violation naming {expected}; got: {violations}"
        )
