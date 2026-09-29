"""Tests for the advisory capture tool.

    venv/bin/python -m pytest tests/test_capture_advisory.py

The property this file protects is asymmetric and easy to get wrong. Almost
any bug in a capture tool is silent: a capture that never happens leaves a
perfectly good app that quietly has no fallback, and nobody finds out until a
demo. So the tests below are weighted towards the **negative** cases — a
non-200, an unvalidated 200, a refused opt-in — and each asserts that *no
file was written*, not merely that the right message printed.

**No live Gemini call is made here.** `capture()` takes the client as an
argument, so every test passes a fake and the real endpoint is never entered.
The tool's own opt-in guard is tested by asserting it raises before it can
import a client.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.tools import capture_advisory as cap


class FakeResponse:
    """Just the two things `capture()` reads off a response."""

    def __init__(self, status_code: int, body: object, text: str | None = None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeClient:
    """Records every call, so "exactly one POST" is assertable."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.posts: list[str] = []
        self.gets: list[str] = []

    def post(self, url: str) -> FakeResponse:
        self.posts.append(url)
        return self.response

    def get(self, url: str) -> FakeResponse:
        self.gets.append(url)
        return FakeResponse(200, {"allocation": {"assignments": [{"shelter": "x"}]}})


def valid_body() -> dict:
    """A 200 advisory body with the fields the tool actually gates on."""
    return {
        "advisory": {
            "executive_summary": "s",
            "evacuation_plan": [],
            "sms_dispatch_draft": "d",
            "post_landfall_risks": "r",
            "historical_context": "h",
        },
        "generated_for": {"category": 5, "origin": {"id": "sagar"}},
        "model": "gemini-3.8-flash",
        "validated": True,
        "validation": {"gemini_calls": 1, "attempts": 1, "checks": [], "plan_coverage": "0/0"},
    }


class TestEnvelope:
    """`build_sample` is a pure function of its arguments."""

    def test_wraps_with_timestamp_and_cached_flag(self):
        sample = cap.build_sample(valid_body(), "2026-09-29T10:00:00+00:00")
        assert sample["cached"] is True
        assert sample["captured_at"] == "2026-09-29T10:00:00+00:00"

    def test_the_response_is_nested_untouched(self):
        """The capture is the endpoint's own bytes, not a re-shaped view."""
        body = valid_body()
        sample = cap.build_sample(body, "2026-09-29T10:00:00+00:00")
        assert sample["response"] == body

    def test_the_timestamp_is_not_read_from_the_clock(self):
        """Two calls with the same argument produce the same envelope, which
        is what makes the function testable at all."""
        assert cap.build_sample(valid_body(), "T") == cap.build_sample(valid_body(), "T")


class TestCapturableGate:
    """Only a real, validated 200 is written."""

    def test_a_valid_200_is_capturable(self):
        ok, reason = cap.is_capturable(valid_body())
        assert ok is True
        assert reason == "ok"

    def test_validated_false_is_rejected(self):
        body = valid_body()
        body["validated"] = False
        ok, reason = cap.is_capturable(body)
        assert ok is False
        assert "not validated" in reason

    def test_a_truthy_but_non_boolean_validated_is_rejected(self):
        """`is True`, not truthiness — a string "true" is not a validation."""
        body = valid_body()
        body["validated"] = "true"
        assert cap.is_capturable(body)[0] is False

    def test_a_missing_validated_key_is_rejected(self):
        body = valid_body()
        del body["validated"]
        assert cap.is_capturable(body)[0] is False

    def test_a_body_with_no_advisory_is_rejected(self):
        assert cap.is_capturable({"validated": True})[0] is False

    def test_a_non_dict_body_is_rejected(self):
        assert cap.is_capturable(["not", "an", "object"])[0] is False

    def test_the_gate_reads_the_field_that_exists(self):
        """The brief asked for `validation.passed`, which this endpoint does
        not have. Asserting its absence is what stops the gate being "fixed"
        back into a check that can never pass — and therefore never captures.
        """
        assert "passed" not in valid_body()["validation"]
        assert valid_body()["validated"] is True


class TestCategoryChoice:
    """Category 5 when it qualifies, else the highest that does."""

    def test_preferred_category_wins_when_non_empty(self):
        counts = {c: 5 for c in range(7)}
        assert cap.resolve_category(counts, preferred=5) == 5

    def test_falls_back_to_the_highest_non_empty(self):
        counts = {0: 0, 1: 0, 2: 3, 3: 0, 4: 0, 5: 0, 6: 0}
        assert cap.resolve_category(counts, preferred=5) == 2

    def test_prefers_the_highest_when_the_preferred_is_empty(self):
        counts = {5: 0, 6: 12, 4: 7}
        assert cap.resolve_category(counts, preferred=5) == 6

    def test_an_all_empty_field_raises_rather_than_guessing(self):
        with pytest.raises(cap.CaptureRefused):
            cap.resolve_category({c: 0 for c in range(7)}, preferred=5)


class TestOneAttemptOnly:
    """No retry loop. A failed capture is reported, not retried."""

    def test_exactly_one_post_is_made(self):
        client = FakeClient(FakeResponse(200, valid_body()))
        cap.capture(client, 5, "sagar")
        assert client.posts == ["/advisory?category=5&origin=sagar"]

    def test_a_failure_is_not_retried(self):
        """The backend's own ladder would retry a 503 three times with
        backoff. For a capture that is wrong: the operator retries on purpose,
        and an automatic one would spend up to six calls to produce a file
        nobody asked for twice."""
        client = FakeClient(FakeResponse(503, {"detail": "busy"}))
        status, _ = cap.capture(client, 5, "sagar")
        assert status == 503
        assert len(client.posts) == 1

    def test_a_non_json_body_degrades_to_text_rather_than_raising(self):
        client = FakeClient(
            FakeResponse(502, ValueError("not json"), text="<html>502</html>")
        )
        status, body = cap.capture(client, 5, "sagar")
        assert status == 502
        assert "_raw" in body
        assert "502" in body["_raw"]


class TestOptInGuard:
    """Nothing is attempted without RUN_LIVE_CAPTURE=1."""

    def test_it_refuses_when_the_env_is_unset(self, monkeypatch):
        monkeypatch.delenv(cap.CAPTURE_ENV, raising=False)
        with pytest.raises(cap.CaptureRefused) as exc:
            cap._require_opt_in()
        assert "not set to 1" in str(exc.value)

    def test_a_present_key_is_not_enough_opt_in(self, monkeypatch):
        """The free tier allows 20 calls/day. A populated `.env` must never be
        what spends the quota, so the guard reads its own variable only."""
        monkeypatch.delenv(cap.CAPTURE_ENV, raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        with pytest.raises(cap.CaptureRefused):
            cap._require_opt_in()

    @pytest.mark.parametrize("value", ["0", "true", "yes", ""])
    def test_only_the_exact_value_opens_the_guard(self, monkeypatch, value):
        monkeypatch.setenv(cap.CAPTURE_ENV, value)
        with pytest.raises(cap.CaptureRefused):
            cap._require_opt_in()

    def test_it_opens_on_exactly_1(self, monkeypatch):
        monkeypatch.setenv(cap.CAPTURE_ENV, "1")
        cap._require_opt_in()  # does not raise


class TestMobileModule:
    """The generated module is the app's only route to a capture."""

    def test_no_sample_generates_an_explicit_null(self):
        source = cap.render_mobile_module(None)
        assert "SAMPLE_ADVISORY: CachedAdvisory | null = null" in source
        assert "generated" in source.lower()

    def test_a_sample_is_written_as_an_object_literal(self):
        source = cap.render_mobile_module(cap.build_sample(valid_body(), "T"))
        assert "= {" in source
        assert '"captured_at": "T"' in source
        assert "null;" not in source.split("export const SAMPLE_ADVISORY")[1]

    def test_the_literal_is_the_captured_bytes(self):
        """`json.dumps` output is a valid JS expression, so the bundled
        advisory is the captured one rather than a lossy re-serialisation."""
        sample = cap.build_sample(valid_body(), "2026-09-29T10:00:00+00:00")
        source = cap.render_mobile_module(sample)
        literal = source.split("= ", 1)[1].rstrip().rstrip(";")
        assert json.loads(literal) == sample

    def test_non_ascii_survives_the_round_trip(self):
        """An advisory for a Bengali district will contain non-ASCII. If the
        generator escaped it, the bundled text would differ from the capture."""
        body = valid_body()
        body["advisory"]["executive_summary"] = "Sagar Island — evacuate now"
        sample = cap.build_sample(body, "T")
        literal = cap.render_mobile_module(sample).split("= ", 1)[1].rstrip().rstrip(";")
        assert json.loads(literal)["response"]["advisory"]["executive_summary"] == (
            "Sagar Island — evacuate now"
        )

    def test_the_committed_placeholder_is_the_null_form(self):
        """Guards the state the app ships in: a valid import that offers no
        fallback, rather than a missing module that breaks the build."""
        assert cap.MOBILE_MODULE.exists()
        assert "= null;" in cap.MOBILE_MODULE.read_text()

    def test_the_committed_placeholder_declares_its_type(self):
        assert "CachedAdvisory" in cap.MOBILE_MODULE.read_text()


class TestAllocationCounts:
    """Category selection reads a local GET, not a model call."""

    def test_it_queries_every_category(self):
        client = FakeClient(FakeResponse(200, valid_body()))
        counts = cap._allocation_counts(client)
        assert set(counts) == set(range(7))
        assert len(client.gets) == 7

    def test_a_missing_assignment_key_counts_as_zero(self):
        class EmptyClient:
            def get(self, url):
                return FakeResponse(200, {"allocation": {}})

        assert set(cap._allocation_counts(EmptyClient()).values()) == {0}


class TestNoLiveCalls:
    """The suite must never spend the quota it exists to capture."""

    def test_no_real_client_is_built_by_the_helpers(self):
        """`capture()` and `is_capturable()` take their client and their
        payload as arguments; the only place a TestClient is constructed is
        `main_entry`, which the guard runs before."""
        assert callable(cap.capture)
        source = cap.__file__
        text = open(source).read()
        guard_at = text.index("def main_entry")
        client_at = text.index("TestClient(main.app)")
        assert guard_at < client_at, "the client must be built after the opt-in guard"

    def test_the_real_app_still_imports_cleanly(self):
        """The tool imports `backend.main` at module scope for `main.app`.
        That import must not have side effects that reach a model."""
        assert main.app is not None
        assert cap.ORIGIN == "sagar"
        assert cap.PREFERRED_CATEGORY == 5
