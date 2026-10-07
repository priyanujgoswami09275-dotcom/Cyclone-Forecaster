"""`/cyclones` stamped its `generated_at` with naive local time.

Every other timestamp in this service is UTC — `/live-cyclone` ends in `Z`,
`/advisory` carries `+00:00`, and both are ISO 8601 with an offset. `/cyclones`
was the one exception: `datetime.now()` returns a naive datetime, so the field
rendered as `2026-10-08T09:14:22` with no offset at all. A client reading it had
to assume UTC (correct only on the developer's machine, or a UTC server) or
guess the server's timezone, and a stamp without an offset cannot be judged
stale against another stamp that has one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_cyclones_generated_at_is_utc_with_an_offset() -> None:
    body = client.get("/cyclones").json()
    stamp = body["generated_at"]
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None, (
        f"generated_at={stamp!r} has no offset: a naive timestamp cannot be "
        "compared against the Z-suffixed stamps the other endpoints return."
    )
    assert parsed.utcoffset() == UTC.utcoffset(None), (
        f"generated_at={stamp!r} is not UTC."
    )


def test_cyclones_agrees_with_the_other_utc_stamps() -> None:
    """The cross-endpoint check that matters: same clock, same spelling.

    Compared as instants, not strings — `/comparison` uses `+00:00` and
    `/live-cyclone` uses `Z`, and both are the same moment. A naive `/cyclones`
    stamp compared against either of them is what produced the ambiguity.
    """
    cyclones = datetime.fromisoformat(client.get("/cyclones").json()["generated_at"])
    comparison = datetime.fromisoformat(
        client.get("/comparison", params={"category": 6}).json()["generated_at"]
    )
    assert cyclones.tzinfo is not None and comparison.tzinfo is not None
    assert abs((cyclones - comparison).total_seconds()) < 60, (
        "the two endpoints' clocks disagree; they read the same UTC clock."
    )
