"""Tests for the `quota` outcome — a spent Gemini daily limit (MEMORY.md §26).

The distinction this file exists for: **"the model is busy" and "the daily
quota is spent" are different failures with different remedies**, and until
now both arrived as a 5xx. A capacity block clears in about a minute and the
identical request is worth repeating. A spent daily quota does not clear at
all — it resets at midnight Pacific — so a client told to "retry shortly"
burns the user's time and the remaining quota to no end.

Concretely, the bug: `google.genai.errors.ClientError` with code 429
(`RESOURCE_EXHAUSTED`) is not a `ServerError`, so it skipped the capacity
retry path entirely and fell through to the blanket `except Exception`,
becoming a 502. On the client, 502 with no `violations` maps to
`kind: 'upstream'` — "something failed upstream", which is not actionable
and, worse, reads as a bug in the app.

No live Gemini calls here. The SDK error is constructed directly and
`generate_advisory` is stubbed to raise it, so the daily quota is untouched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from google.genai.errors import ClientError

from backend import main

CATEGORY = 6
ORIGIN = "kakdwip"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


def make_quota_error(message: str = "Quota exceeded for quota metric 'Generate requests'") -> ClientError:
    """The error the SDK raises when the daily free-tier limit is spent.

    Constructed, not raised by a real call. 429 with the
    `RESOURCE_EXHAUSTED` status word, which is how the Gemini API reports a
    daily limit as distinct from a per-minute 429.
    """
    return ClientError(
        code=429,
        response_json={
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": message,
            }
        },
    )


@pytest.fixture
def quota_env(monkeypatch):
    """A key, and a stubbed generate_advisory that raises the quota error."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    def boom(*a, **k):
        raise make_quota_error()

    monkeypatch.setattr(main, "generate_advisory", boom)
    return monkeypatch


class TestDetection:
    """429 / RESOURCE_EXHAUSTED is quota, and quota is not capacity."""

    def test_a_429_client_error_is_quota(self):
        assert main._is_quota_error(make_quota_error()) is True

    def test_a_503_is_not_quota(self):
        from google.genai.errors import ServerError

        server_error = ServerError(code=503, response_json={"error": {"status": "UNAVAILABLE"}})
        assert main._is_quota_error(server_error) is False
        assert main._is_capacity_error(server_error) is True

    def test_the_two_do_not_overlap(self):
        """A retry is correct for one and wrong for the other, so an error
        must never satisfy both predicates."""
        quota = make_quota_error()
        assert main._is_quota_error(quota) and not main._is_capacity_error(quota)

    def test_an_ordinary_client_error_is_not_quota(self):
        other = ClientError(code=400, response_json={"error": {"status": "INVALID_ARGUMENT"}})
        assert main._is_quota_error(other) is False

    def test_a_capacity_block_is_not_retried_as_quota(self):
        """The capacity path retries. If a 503 also read as quota the client
        would be told to wait for midnight when a minute would do."""
        from google.genai.errors import ServerError

        assert main._is_quota_error(ServerError(code=503, response_json={})) is False


class TestEndpointOutcome:
    """A spent quota is 429, named, and carries no Retry-After."""

    def test_a_spent_quota_is_429(self, client, quota_env):
        response = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert response.status_code == 429

    def test_it_is_no_longer_a_502(self, client, quota_env):
        """The regression this file was written for. 502 maps to
        `kind: 'upstream'` on the client — true but useless."""
        response = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert response.status_code != 502
        assert response.status_code != 500

    def test_the_detail_says_the_daily_limit_is_reached(self, client, quota_env):
        detail = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}").json()["detail"]
        lowered = detail.lower()
        assert "daily" in lowered
        assert "limit" in lowered or "quota" in lowered
        assert "midnight" in lowered

    def test_the_detail_names_pacific(self, client, quota_env):
        """Which midnight matters: Gemini's free tier resets at midnight
        Pacific, and telling a user to wait for their own midnight sends them
        to bed for eight hours."""
        detail = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}").json()["detail"]
        assert "pacific" in detail.lower()

    def test_no_retry_after_header(self, client, quota_env):
        """Deliberate. `Retry-After: 60` would be a lie — a minute changes
        nothing, and a client that honours it would poll all night."""
        response = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert "retry-after" not in {k.lower() for k in response.headers}

    def test_it_is_not_retried_across_capacity_attempts(self, client, quota_env):
        """Spending three more calls (and more quota) on a limit that will not
        move is the exact waste this outcome exists to stop."""
        calls = []

        def counting(*a, **k):
            calls.append(1)
            raise make_quota_error()

        quota_env.setattr(main, "generate_advisory", counting)
        response = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert response.status_code == 429
        assert len(calls) == 1, f"expected one attempt, made {len(calls)}"


class TestCapacityPathUntouched:
    """Decision: the capacity path stays 503 + Retry-After. Guarded so a
    future edit cannot quietly make the two outcomes identical."""

    @pytest.fixture
    def capacity_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setattr(main, "_sleep", lambda _s: None)

        def boom(*a, **k):
            raise main.GeminiCapacityError("model busy")

        monkeypatch.setattr(main, "generate_advisory", boom)
        return monkeypatch

    def test_capacity_is_still_503(self, client, capacity_env):
        response = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert response.status_code == 503

    def test_capacity_still_sends_retry_after(self, client, capacity_env):
        response = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert response.headers.get("retry-after") == "60"

    def test_a_plain_sdk_failure_is_still_502(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

        def boom(*a, **k):
            raise RuntimeError("something else entirely")

        monkeypatch.setattr(main, "generate_advisory", boom)
        assert client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}").status_code == 502


class TestNoLiveCalls:
    """The suite must never spend the daily quota it is testing for."""

    def test_no_live_gemini_client_is_constructed(self, quota_env):
        """`generate_advisory` was stubbed, so the code path that builds a
        real `genai.Client` was never entered."""
        assert callable(main.generate_advisory)
        # The stub is what's bound; the real one is not on this path.
        assert main.generate_advisory.__name__ == "boom"
