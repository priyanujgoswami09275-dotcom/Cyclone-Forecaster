"""The /routes blocked-edge cache: same wind, same result, one computation.

`flooded_edges` tests ~90k graph edges against the flood polygon on every
/routes call. This test verifies that the second call at the same wind does
NOT recompute them, and that the route output is identical before and after
the cache for at least three origins at two strengths.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.simulation import routing

client = TestClient(main.app)

ORIGINS = ["namkhana", "kakdwip", "sagar"]
CATEGORIES = [5, 6]


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset the blocked-edge cache between tests so each starts clean."""
    blocked_cache_clear()
    yield
    blocked_cache_clear()


def blocked_cache_clear():
    """Clear any lru_cache that holds blocked-edge results."""
    for name in ["_blocked_edges_for_wind", "flood_for_scenario"]:
        fn = getattr(main, name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("origin", ORIGINS)
def test_route_identical_across_cache_states(category, origin):
    """The route (coordinates, length, reachable, reason) must be identical
    whether it was computed fresh or served from the blocked-edge cache."""
    # First call: computes from scratch
    r1 = client.get(f"/routes?category={category}&origin={origin}")
    assert r1.status_code == 200
    body1 = r1.json()

    # Second call: served from the blocked-edge cache
    r2 = client.get(f"/routes?category={category}&origin={origin}")
    assert r2.status_code == 200
    body2 = r2.json()

    # The route figures must be byte-identical
    for key in ("coordinates", "length_m", "reachable", "reason"):
        assert body1[key] == body2[key], (
            f"route {key} changed between fresh and cached for "
            f"category={category}, origin={origin}"
        )


def test_flooded_edges_called_once_per_wind(monkeypatch):
    """The second /routes call at the same wind must not re-run flooded_edges."""
    calls = []
    real = routing.flooded_edges

    def counting(graph, flood):
        calls.append(1)
        return real(graph, flood)

    # Patch in BOTH namespaces: _blocked_edges_for_wind resolves `flooded_edges`
    # from main's imported reference, not from the routing module directly.
    monkeypatch.setattr(main, "flooded_edges", counting)
    monkeypatch.setattr(routing, "flooded_edges", counting)

    # First request: computes (calls flooded_edges)
    r1 = client.get("/routes?category=6&origin=namkhana")
    assert r1.status_code == 200
    assert len(calls) >= 1

    # Second request at the same wind: cache hit (no call to flooded_edges)
    calls.clear()
    r2 = client.get("/routes?category=6&origin=kakdwip")
    assert r2.status_code == 200
    assert len(calls) == 0, (
        f"flooded_edges was called {len(calls)} times on the second request; "
        f"the blocked-edge cache should have served it"
    )
