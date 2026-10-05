"""`registry().live_status()` must be cached for a short window, not per call.

Repeated app opens must not hammer the live probe each time, and a cached
`LiveStatus` is a real one cached. The clock is injected so this test can
measure a real window expiry rather than racing a real timer.
"""

from __future__ import annotations

from backend.cyclones.base import LiveStatus
from backend.cyclones.registry import CycloneRegistry


class _CountingSource:
    """A duck-typed source that always answers and counts how often."""

    identifier = "stub"
    endpoints = ("https://example",)

    def __init__(self) -> None:
        self.calls = 0

    async def probe(self) -> LiveStatus:
        self.calls += 1
        return LiveStatus(
            status="no_active_storm",
            source=self.identifier,
            http_status=200,
            reason="ok",
            checked_at="2026-10-06T00:00:00Z",
            endpoints=self.endpoints,
        )


def test_two_calls_within_sixty_seconds_probe_once() -> None:
    clock = [0.0]
    source = _CountingSource()
    registry = CycloneRegistry(source=source, now=lambda: clock[0], live_ttl_seconds=60.0)

    first = registry.live_status()
    clock[0] = 30.0
    second = registry.live_status()

    assert source.calls == 1
    # Cached: it must be the very same immutable result, not a re-probe.
    assert second is first


def test_call_after_the_window_probes_again_and_caches() -> None:
    clock = [0.0]
    source = _CountingSource()
    registry = CycloneRegistry(source=source, now=lambda: clock[0], live_ttl_seconds=60.0)

    registry.live_status()
    clock[0] = 59.9
    assert source.calls == 1
    registry.live_status()
    assert source.calls == 1

    clock[0] = 60.0
    registry.live_status()
    assert source.calls == 2

    # The fresh answer is itself cached.
    clock[0] = 60.0 + 30.0
    registry.live_status()
    assert source.calls == 2
