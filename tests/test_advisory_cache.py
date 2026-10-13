"""Tests for the /advisory successful-result cache.

The cache exists because the free Gemini tier allows ~20 requests/day and
the endpoint is public. These tests patch the Gemini generation function
(`_generate_with_capacity_retry`), never the network, and verify:

1. A second identical request makes zero extra Gemini calls and returns
   `served_from_cache: true`.
2. A cache hit has `gemini_calls: 0` and the same `generated_at`.
3. A different origin or different wind misses.
4. TTL expiry (advance the injected clock) misses.
5. A failed call (quota, 503) is not cached and the next call retries.
6. A response with remaining violations is not cached.
7. A miss has `served_from_cache: false` and a fresh `generated_at`.
8. The 429 response still has NO Retry-After header.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.ai.advisory import DistrictAdvisory, EvacuationPriority

CATEGORY = 6
ORIGIN = "kakdwip"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


class FakeClock:
    """A monotonic clock the tests can advance past the TTL."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


@pytest.fixture
def fresh_cache(monkeypatch):
    """Replace the module-level cache with a test instance using a fake clock."""
    clock = FakeClock()
    test_cache = main.AdvisoryCache(clock=clock)
    monkeypatch.setattr(main, "_advisory_cache", test_cache)
    return test_cache, clock


@pytest.fixture
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-for-cache-tests")


def make_valid_advisory(allocation: dict) -> DistrictAdvisory:
    """A minimal advisory that passes every validate_advisory check."""
    localities = [row["node"] for row in allocation["allocation"]]
    plan = [
        EvacuationPriority(
            locality_name=name,
            priority_level="HIGH",
            reasoning="Placeholder reasoning for test coverage.",
        )
        for name in localities
    ]
    return DistrictAdvisory(
        executive_summary="Provisional advisory based on placeholder shelter data.",
        evacuation_plan=plan,
        sms_dispatch_draft="Evacuate to provisional shelters. Placeholder data.",
        post_landfall_risks="Freshwater flooding and salinization expected.",
        historical_context="Wind and surge comparable to Cyclone Amphan 2020.",
    )


@pytest.fixture
def counting_gemini(monkeypatch):
    """Patch the Gemini retry ladder to return valid advisories and count calls."""

    calls: list[int] = []

    def mock_retry(*args, **kwargs):
        calls.append(1)
        # args[2] is the allocation payload; build a valid advisory from it
        allocation = args[2] if len(args) > 2 else kwargs.get("allocation", {})
        return make_valid_advisory(allocation), len(calls)

    monkeypatch.setattr(main, "_generate_with_capacity_retry", mock_retry)
    return calls


class TestAdvisoryCacheHit:
    def test_second_request_hits_cache(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        """The second identical request makes zero extra Gemini calls."""
        r1 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r1.status_code == 200
        assert r1.json()["served_from_cache"] is False
        assert len(counting_gemini) == 1

        r2 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r2.status_code == 200
        assert r2.json()["served_from_cache"] is True
        assert len(counting_gemini) == 1, "cache hit must not call Gemini"

    def test_hit_has_zero_gemini_calls_and_same_generated_at(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        r1 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        r2 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        b1, b2 = r1.json(), r2.json()
        assert b2["validation"]["gemini_calls"] == 0
        assert b2["generated_at"] == b1["generated_at"], (
            "generated_at must be the original time, never refreshed"
        )
        assert b2["validation"]["attempts"] == b1["validation"]["attempts"]

    def test_scenario_id_override_on_hit(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        """On a cache hit, generated_for must show the CURRENT request's
        scenario_id and category, not whichever scenario was first cached.

        The cache key is the resolved wind, so two different scenario ids
        that resolve to the same wind share an entry. In the real catalogue
        no two scenario ids for the same cyclone share a wind (cat6=222,
        cat5=194, observed=111.1), so this test patches the stored entry's
        key to match cat5's wind — simulating the collision the code
        handles. (Patching the resolved wind, as approved.)
        """
        # Request with cat6 (wind 222) → cached
        r1 = client.post(f"/advisory?category=6&origin={ORIGIN}")
        assert r1.json()["served_from_cache"] is False
        assert r1.json()["generated_for"]["scenario_id"] == "cat6"
        assert r1.json()["generated_for"]["category"] == 6

        # Patch the stored key's wind from 222 (cat6) to 194 (cat5)
        # so the next request with cat5 finds the same entry.
        cache = fresh_cache[0]
        old_key = list(cache._store.keys())[0]
        entry = cache._store.pop(old_key)
        cache._order.remove(old_key)
        new_key = (old_key[0], 194.0, old_key[2], old_key[3])
        cache._store[new_key] = entry
        cache._order.append(new_key)

        # Request with cat5 (wind 194) → finds the patched entry
        r2 = client.post(f"/advisory?category=5&origin={ORIGIN}")
        assert r2.json()["served_from_cache"] is True
        assert r2.json()["generated_for"]["scenario_id"] == "cat5", (
            "the hit must show the CURRENT request's scenario_id (cat5), "
            "not the originally cached one (cat6)"
        )
        assert r2.json()["generated_for"]["category"] == 5, (
            "the hit must show the CURRENT request's category (5), not 6"
        )

    def test_cache_is_copy_safe(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        """A cache hit must not mutate the stored entry.

        The hit path sets served_from_cache=True, gemini_calls=0, and
        overwrites generated_for.scenario_id/category on the returned copy.
        Without deepcopy, those mutations would corrupt the stored entry and
        the next hit would inherit them. This test inspects the cache's
        internal state directly after a hit.
        """
        # First request → cached
        r1 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r1.json()["served_from_cache"] is False

        # Get the stored entry directly
        cache = fresh_cache[0]
        key = list(cache._store.keys())[0]

        # Second request → hit (mutates the RETURNED copy, not the stored one)
        r2 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r2.json()["served_from_cache"] is True

        # Verify the STORED entry is unchanged by the hit
        stored = cache._store[key]["body"]
        assert stored["served_from_cache"] is False, (
            "the stored entry must still say served_from_cache: false — "
            "a hit mutated the stored copy"
        )
        assert stored["validation"]["gemini_calls"] != 0, (
            "the stored entry's gemini_calls must not be zeroed by a hit"
        )


class TestAdvisoryCacheMiss:
    def test_different_origin_misses(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        r2 = client.post(f"/advisory?category={CATEGORY}&origin=namkhana")
        assert r2.json()["served_from_cache"] is False
        assert len(counting_gemini) == 2

    def test_different_wind_misses(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        r2 = client.post(f"/advisory?category=5&origin={ORIGIN}")
        assert r2.json()["served_from_cache"] is False
        assert len(counting_gemini) == 2

    def test_ttl_expiry_misses(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        cache, clock = fresh_cache
        client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert len(counting_gemini) == 1

        clock.advance(7 * 3600)  # past the 6-hour TTL
        r2 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r2.json()["served_from_cache"] is False
        assert len(counting_gemini) == 2, "expired entry must miss"

    def test_miss_has_fresh_generated_at(
        self, client, fresh_cache, gemini_env, counting_gemini
    ):
        r1 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        clock = fresh_cache[1]
        clock.advance(7 * 3600)
        r2 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r2.json()["served_from_cache"] is False
        assert r2.json()["generated_at"] != r1.json()["generated_at"]


class TestAdvisoryCacheExclusions:
    def test_failed_call_not_cached(
        self, client, fresh_cache, gemini_env, monkeypatch
    ):
        """A quota failure is not cached; the next call retries."""
        from google.genai.errors import ClientError

        def quota_error(*args, **kwargs):
            raise main.GeminiQuotaError("Daily limit reached")

        monkeypatch.setattr(main, "_generate_with_capacity_retry", quota_error)
        r1 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r1.status_code == 429

        # Now replace with a working mock — the next call should succeed
        # (not served from cache, because the failure was not cached).
        calls: list[int] = []

        def working(*args, **kwargs):
            calls.append(1)
            allocation = args[2] if len(args) > 2 else {}
            return make_valid_advisory(allocation), 1

        monkeypatch.setattr(main, "_generate_with_capacity_retry", working)
        r2 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r2.status_code == 200
        assert r2.json()["served_from_cache"] is False
        assert len(calls) == 1

    def test_violation_response_not_cached(
        self, client, fresh_cache, gemini_env, monkeypatch
    ):
        """A response with remaining violations is not cached."""
        call_count = [0]

        def always_bad(*args, **kwargs):
            call_count[0] += 1
            # Always return an advisory that fails validation (bad locality)
            return DistrictAdvisory(
                executive_summary="Provisional advisory with placeholder data.",
                evacuation_plan=[
                    EvacuationPriority(
                        locality_name="INVENTED_PLACE",
                        priority_level="HIGH",
                        reasoning="This will fail validation.",
                    )
                ],
                sms_dispatch_draft="Placeholder.",
                post_landfall_risks="Flooding.",
                historical_context="Cyclone Amphan 2020.",
            ), 1

        monkeypatch.setattr(main, "_generate_with_capacity_retry", always_bad)
        # Both the first draft AND the correction pass return the same bad
        # advisory, so the endpoint 502s with the violations listed.
        r1 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r1.status_code == 502
        assert call_count[0] == 2  # draft + correction

        # The failure was not cached — a new request with a good advisory
        # must be a fresh miss, not served from a cached 502.
        def always_good(*args, **kwargs):
            allocation = args[2] if len(args) > 2 else {}
            return make_valid_advisory(allocation), 1

        monkeypatch.setattr(main, "_generate_with_capacity_retry", always_good)
        r2 = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r2.status_code == 200
        assert r2.json()["served_from_cache"] is False


class TestQuota429Untouched:
    def test_429_still_has_no_retry_after(
        self, client, fresh_cache, gemini_env, monkeypatch
    ):
        """The 429 path is untouched: no Retry-After, same detail."""

        def quota_error(*args, **kwargs):
            raise main.GeminiQuotaError("Daily limit reached")

        monkeypatch.setattr(main, "_generate_with_capacity_retry", quota_error)
        r = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r.status_code == 429
        assert "retry-after" not in {k.lower() for k in r.headers}
