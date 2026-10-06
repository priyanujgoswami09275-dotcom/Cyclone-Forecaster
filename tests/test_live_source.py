"""The live provider's failure paths.

Every test here is a way the live feed can fail to say anything about the North
Indian Ocean, and each one must produce an explicit state rather than a
substitute. The last two tests are the ones that matter most: they assert the
provider never reaches for historical data.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from backend.cyclones.base import LIVE_UNAVAILABLE_REASON, NO_ACTIVE_STORM_REASON
from backend.cyclones.live import DEFAULT_ATCF_ENDPOINTS, AtcfLiveSource

FIXTURE = Path(__file__).parent / "fixtures" / "aal012025_sample.dat"


def run(coro):
    """Drive a coroutine. The provider is async for the API's sake only."""
    return asyncio.run(coro)


def fake_transport(status: int = 200, body: str = "", raises: Exception | None = None):
    def transport(url: str, timeout: float) -> tuple[int, str]:
        if raises is not None:
            raise raises
        return status, body

    return transport


# --- the state a judge actually sees ----------------------------------------


def test_the_default_endpoints_are_real_urls() -> None:
    """They return 403/404 today. They are the right URLs, not placeholders."""
    assert DEFAULT_ATCF_ENDPOINTS
    for url in DEFAULT_ATCF_ENDPOINTS:
        assert url.startswith("https://"), url
    assert any("nrlmry" in u for u in DEFAULT_ATCF_ENDPOINTS)
    assert any("nhc.noaa.gov" in u for u in DEFAULT_ATCF_ENDPOINTS)


def test_live_unavailable_carries_the_promise_not_just_the_diagnostic() -> None:
    """The reason must say what is NOT being shown, before anything else.

    A bare "tried 4 sources: 403, 403, 404, 200" is an engineer's sentence. The
    reader needs to know no historical cyclone is standing in for the live one,
    and that has to be in the string the API ships rather than in this file.
    """
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/a.txt",),
            transport=fake_transport(status=403),
        ).probe()
    )
    assert status.status == "live_unavailable"
    assert status.http_status == 403
    assert LIVE_UNAVAILABLE_REASON in status.reason
    assert status.checked_at in status.reason, "the attempt's time must travel with it"
    # And the engineering detail rides alongside, not instead of.
    assert "403" in status.reason


def test_every_failure_state_carries_the_promise() -> None:
    """Including `no_active_storm`, which is the state most easily misread.

    "No storm running" read as a working live feed is the failure this whole
    provider is built to avoid, so it gets the same guarantee.
    """
    for kwargs in (
        dict(status=403),
        dict(status=404),
        dict(status=0),
        dict(raises=TimeoutError("timed out")),
    ):
        status = run(
            AtcfLiveSource(
                endpoints=("https://example.test/x",), transport=fake_transport(**kwargs)
            ).probe()
        )
        assert LIVE_UNAVAILABLE_REASON in status.reason, kwargs
        assert status.status == "live_unavailable", kwargs
    # The quiet-basin state is a different promise, not the unavailable one.
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",),
            transport=fake_transport(status=200, body=""),
        ).probe()
    )
    assert NO_ACTIVE_STORM_REASON in status.reason
    assert LIVE_UNAVAILABLE_REASON not in status.reason


# --- the states themselves --------------------------------------------------


def test_a_403_is_live_unavailable_with_the_status() -> None:
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/storms.txt",),
            transport=fake_transport(status=403),
        ).probe()
    )
    assert status.status == "live_unavailable"
    assert status.http_status == 403


def test_a_404_is_live_unavailable() -> None:
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",), transport=fake_transport(status=404)
        ).probe()
    )
    assert status.status == "live_unavailable"
    assert status.http_status == 404


def test_a_transport_exception_is_live_unavailable_and_mentions_it() -> None:
    """A timeout is the common case behind a firewall, and it is not silence."""
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",),
            transport=fake_transport(raises=TimeoutError("timed out")),
        ).probe()
    )
    assert status.status == "live_unavailable"
    assert "timed out" in status.reason.lower() or "0" in status.reason


def test_no_active_storm_is_distinct_from_unavailable() -> None:
    """The source answered; there was simply nothing running.

    Collapsing this into `live_unavailable` would tell a reader the feed is
    broken when it is working perfectly and the basin is quiet.
    """
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",), transport=fake_transport(status=200, body="")
        ).probe()
    )
    assert status.status == "no_active_storm"


def test_no_active_storm_reason_does_not_claim_the_feed_was_unreachable() -> None:
    """"The feed answered; the basin is quiet. Those are not the same sentence.

    The reason used to open with the `live_unavailable` claim — "could not be
    reached" — because it reused that constant verbatim. A quiet basin told the
    reader the working feed was broken.
    """
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",),
            transport=fake_transport(status=200, body=""),
        ).probe()
    )
    assert status.status == "no_active_storm"
    assert "could not be reached" not in status.reason
    # The same guarantees the unavailable reason carries, still delivered:
    assert status.checked_at in status.reason, "the attempt's time must travel with it"
    assert "No historical or case-study cyclone is being substituted" in status.reason
    # And the diagnostic rides alongside, not instead of.
    assert "Every source answered in ATCF" in status.reason


def test_a_200_that_is_not_atcf_is_unavailable_not_a_fabricated_storm() -> None:
    """An HTML maintenance page must not become a cyclone."""
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",),
            transport=fake_transport(status=200, body="<html>maintenance</html>"),
        ).probe()
    )
    assert status.status == "live_unavailable"


def test_an_atlantic_feed_is_not_a_north_indian_ocean_storm() -> None:
    """The real NHC bytes, asked the wrong basin.

    This is the most spectacular wrong answer available from this feature:
    a Caribbean hurricane drawn over the Sundarbans delta.
    """
    source = AtcfLiveSource(
        endpoints=("https://example.test/x",),
        transport=fake_transport(status=200, body=FIXTURE.read_text()),
    )
    status = run(source.probe())
    assert status.status == "no_active_storm"
    assert run(source.fetch()) is None


# --- the substitute that must never happen ----------------------------------


def test_fetch_returns_none_when_unreachable() -> None:
    assert (
        run(
            AtcfLiveSource(
                endpoints=("https://example.test/x",), transport=fake_transport(status=403)
            ).fetch()
        )
        is None
    )


def test_fetch_never_returns_remal_or_any_historical_record() -> None:
    """The architectural rule, as an assertion rather than a comment.

    `backend/cyclones/live.py` must not import the historical source. If someone
    adds a fallback so that "live mode still shows something", this fails.
    """
    import ast

    live_source = Path(__file__).resolve().parents[1] / "backend" / "cyclones" / "live.py"
    tree = ast.parse(live_source.read_text())

    # Imports and executable symbol references only. Prose is exempt: this
    # module's docstring explains the rule by naming what it refuses, and a
    # grep for the word "historical" would forbid the explanation too.
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            imported.add(node.id)
        elif isinstance(node, ast.Attribute):
            imported.add(node.attr)

    for forbidden in ("historical", "IbtracsSource", "read_ni_rows", "build_catalogue"):
        assert forbidden not in imported, (
            f"live.py references {forbidden!r}. It must never be able to reach "
            f"historical data: a live mode showing Remal is worse than no live mode."
        )


# --- the happy path, from a synthetic IO fix --------------------------------

IO_FIX = "IO, 01, 2025062218, 01, TEST, 0, 152N, 845E, 45, 990, TS,"


def test_a_real_io_line_becomes_a_live_record() -> None:
    source = AtcfLiveSource(
        endpoints=("https://example.test/x",),
        transport=fake_transport(status=200, body=IO_FIX),
    )
    record = run(source.fetch())
    assert record is not None
    assert record.source == "atcf_live"
    assert record.observed is True
    assert record.basin == "IO"
    assert record.cyclone_id.startswith("IO")
    assert len(record.waypoints) == 1
    assert record.data_through == "2025-06-22 18:00:00"


def test_a_live_record_discloses_what_atcf_wind_actually_is() -> None:
    """ATCF wind is a 1-minute mean; the app's bands are 3-minute IMD means."""
    source = AtcfLiveSource(
        endpoints=("https://example.test/x",),
        transport=fake_transport(status=200, body=IO_FIX),
    )
    record = run(source.fetch())
    assert record is not None
    assert "1-minute mean" in record.limitation
    assert "not a forecast" in record.limitation
    assert "knots" in record.limitation


# --- protocol conformance ---------------------------------------------------


def test_it_satisfies_the_cyclone_source_protocol() -> None:
    from backend.cyclones.base import CycloneSource

    assert isinstance(AtcfLiveSource(), CycloneSource)


def test_the_probe_response_serialises() -> None:
    """`GET /live-cyclone` serves `to_dict()` verbatim, so it must be JSON."""
    import json

    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",), transport=fake_transport(status=403)
        ).probe()
    )
    payload = json.loads(json.dumps(status.to_dict()))
    assert set(payload) == {
        "status",
        "source",
        "http_status",
        "reason",
        "checked_at",
        "endpoints",
        # Added in T7: the storm itself, `None` whenever there is not one.
        "cyclone",
    }


def test_every_endpoint_is_tried_before_giving_up() -> None:
    """An operator who puts a working source second must not be defeated by the
    first one's 403."""
    tried: list[str] = []

    def transport(url: str, timeout: float) -> tuple[int, str]:
        tried.append(url)
        return 403, ""

    status = run(
        AtcfLiveSource(endpoints=("https://a.test", "https://b.test"), transport=transport).probe()
    )
    assert tried == ["https://a.test", "https://b.test"]
    assert status.endpoints == ("https://a.test", "https://b.test")
    assert status.status == "live_unavailable"


def test_a_working_second_source_is_found() -> None:
    calls = {"n": 0}

    def transport(url: str, timeout: float) -> tuple[int, str]:
        calls["n"] += 1
        return (403, "") if calls["n"] == 1 else (200, IO_FIX)

    source = AtcfLiveSource(
        endpoints=("https://a.test", "https://b.test"), transport=transport
    )
    status = run(source.probe())
    assert status.status == "available"
    assert "b.test" in status.source

def test_the_live_record_id_comes_from_the_storm_number() -> None:
    """The probe must not invent an id like IO-live-2025 for a real storm."""
    body = "IO, 05, 2025062218, 01, CARQ, 0, 152N, 845E, 45, 990, TS,\n"
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",),
            transport=fake_transport(status=200, body=body),
        ).probe()
    )
    assert status.status == "available"
    assert status.cyclone["cyclone_id"] == "IO052025"
    assert "-live-" not in status.cyclone["cyclone_id"]


def test_a_fix_without_a_storm_number_yields_no_record() -> None:
    """An honest absence over a fabricated identity."""
    body = "IO, , 2025062218, 01, CARQ, 0, 152N, 845E, 45, 990, TS,\n"
    status = run(
        AtcfLiveSource(
            endpoints=("https://example.test/x",),
            transport=fake_transport(status=200, body=body),
        ).probe()
    )
    assert status.status != "available"
    assert status.cyclone is None
