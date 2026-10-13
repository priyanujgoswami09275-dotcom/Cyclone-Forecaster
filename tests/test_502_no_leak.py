"""The 502 detail must not leak exception text to the client.

A missing API key reads `KeyError: 'GEMINI_API_KEY'` and SDK errors can echo
upstream text. The blanket `except Exception` in both /advisory and
/risk-analyst formats the exception into the response body; the mobile client
shows that string verbatim as the error message. These tests verify the fix:
the full exception is logged server-side, but the client sees a fixed,
plain-language sentence.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main

CATEGORY = 6
ORIGIN = "kakdwip"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    main._advisory_cache._store.clear()
    main._advisory_cache._order.clear()


class TestAdvisory502DoesNotLeak:
    @pytest.mark.parametrize("exc_type,exc_msg", [
        (KeyError, "GEMINI_API_KEY"),
        (RuntimeError, "secret upstream text from the SDK"),
        (ValueError, "Gemini returned no schema-valid DistrictAdvisory"),
    ])
    def test_exception_text_absent(self, client, env, monkeypatch, exc_type, exc_msg):
        """No exception name or message reaches the client."""

        def boom(*a, **k):
            raise exc_type(exc_msg)

        monkeypatch.setattr(main, "_generate_with_capacity_retry", boom)
        r = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert exc_msg not in detail, f"exception text leaked: {detail}"
        assert exc_type.__name__ not in detail, f"exception name leaked: {detail}"

    def test_fixed_detail_is_plain(self, client, env, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("something exploded internally")

        monkeypatch.setattr(main, "_generate_with_capacity_retry", boom)
        r = client.post(f"/advisory?category={CATEGORY}&origin={ORIGIN}")
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert "Gemini advisory generation failed" in detail
        assert "upstream or configuration fault" in detail


class TestRiskAnalyst502DoesNotLeak:
    @pytest.mark.parametrize("exc_type,exc_msg", [
        (KeyError, "GEMINI_API_KEY"),
        (RuntimeError, "secret upstream text from the SDK"),
    ])
    def test_exception_text_absent(self, client, env, monkeypatch, exc_type, exc_msg):
        def boom(*a, **k):
            raise exc_type(exc_msg)

        monkeypatch.setattr(main, "_generate_with_capacity_retry", boom)
        r = client.post("/risk-analyst", json={"category": CATEGORY, "origin": ORIGIN})
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert exc_msg not in detail, f"exception text leaked: {detail}"
        assert exc_type.__name__ not in detail, f"exception name leaked: {detail}"

    def test_fixed_detail_is_plain(self, client, env, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("something exploded internally")

        monkeypatch.setattr(main, "_generate_with_capacity_retry", boom)
        r = client.post("/risk-analyst", json={"category": CATEGORY, "origin": ORIGIN})
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert "Gemini risk analysis failed" in detail
        assert "upstream or configuration fault" in detail
