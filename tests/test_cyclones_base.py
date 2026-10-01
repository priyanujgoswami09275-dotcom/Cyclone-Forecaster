"""Tests for the normalized cyclone vocabulary — `backend/cyclones/base.py`.

Every later task in the dynamic-cyclone plan speaks this vocabulary: IBTrACS
historical records (T2), the ATCF live source (T3), the registry (T5), the
scenario catalogue (T5) and the six new endpoints (T7). Two of those tasks
serialize these objects to JSON, so `to_dict()` is tested here against
`json.dumps` rather than being re-derived in each of them.

What is worth protecting, and why
---------------------------------
1. **A missing wind is not a zero wind.** IBTrACS writes a blank `USA_WIND` as
   `0.0`, so a fix can carry a number that means "not reported". `peak_wind_kmph`
   filters on `wind_reported`, never on the value being non-zero — dropping
   reported zeros would understate a track that weakens to nothing, and turning
   them into zeros would invent a calm. Both directions are tested (MEMORY.md
   §31, and the note beside `peakReportedWindKmph` in `main.py`).
2. **`LiveStatus` has three states, not two.** `no_active_storm` means the
   source answered and there is nothing active; `live_unavailable` means we could
   not get a trustworthy answer. Collapsing them would tell a judge that a
   working feed was broken. The set is closed at the type level.
3. **`LIVE_UNAVAILABLE_REASON` promises no substitute.** This is the sentence
   shown when no live feed can be reached. It has to say the feed is down, that
   nothing is standing in for it, and that the historical list and the
   deterministic simulation are untouched — because a reader who suspects the
   app quietly fell back to Remal would be right to be suspicious of it.
4. **The source protocol has exactly one signature.** T3's `fetch` and T2's
   `identifier` must match it or the registry has two dialects.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import typing

import pytest

from backend.cyclones.base import (
    LIVE_UNAVAILABLE_REASON,
    CycloneRecord,
    CycloneSource,
    CycloneWaypoint,
    LiveStatus,
    peak_wind_kmph,
)


def _waypoint(iso_time: str = "2024-05-25T12:00:00Z", **overrides) -> CycloneWaypoint:
    """One best-track fix, with field overrides, so a test reads as a delta."""
    fields = {
        "iso_time": iso_time,
        "latitude": 19.2,
        "longitude": 89.2,
        "wind_kmph": 35.0,
        "wind_reported": True,
        "pressure_hpa": None,
        "nature": "TS",
    }
    fields.update(overrides)
    return CycloneWaypoint(**fields)


def _record(**overrides) -> CycloneRecord:
    """A minimal two-fix record; overrides let a test state only what differs."""
    fields = {
        "cyclone_id": "2024145N14087",
        "name": "REMAL",
        "season": 2024,
        "basin": "N",
        "subbasin": None,
        "source": "ibtracs_v04r01_ni",
        "observed": True,
        "waypoints": (
            _waypoint(),
            _waypoint("2024-05-25T15:00:00Z", latitude=19.4, wind_kmph=None,
                      wind_reported=False),
        ),
        "fetched_at": "2026-10-01T00:00:00Z",
        "data_through": "2024-05-26T18:00:00Z",
        "peak_wind_kmph": 35.0,
        "limitation": "Screening estimate, not a forecast.",
    }
    fields.update(overrides)
    return CycloneRecord(**fields)


# --------------------------------------------------------------------------
# peak_wind_kmph
# --------------------------------------------------------------------------


def test_peak_wind_ignores_unreported_fixes():
    # A waypoint with wind_reported False must not contribute, and must not
    # become 0. This is the rule /track already implements (MEMORY.md §31).
    ws = (
        CycloneWaypoint("2024-05-25T12:00:00Z", 19.2, 89.2, 35.0, True, None, "TS"),
        CycloneWaypoint("2024-05-25T15:00:00Z", 19.4, 89.2, None, False, None, "TS"),
    )
    assert peak_wind_kmph(ws) == 35.0


def test_peak_wind_is_none_when_nothing_was_reported():
    ws = (CycloneWaypoint("2024-05-25T12:00:00Z", 19.2, 89.2, None, False, None, "TS"),)
    assert peak_wind_kmph(ws) is None


def test_peak_wind_keeps_a_reported_zero():
    """The other direction, and the reason this is a function and not a max().

    A best-track agency genuinely reports 0 kt for a dissipated system. Reading
    that as "not reported" would erase the end of a track that weakened to
    nothing, and the resulting peak would be a wind the storm never had at the
    point it mattered. So the filter is on `wind_reported`, never on the value.
    """
    ws = (
        _waypoint(wind_kmph=35.0, wind_reported=True),
        _waypoint("2024-05-25T15:00:00Z", wind_kmph=0.0, wind_reported=True),
    )
    assert peak_wind_kmph(ws) == 35.0
    assert peak_wind_kmph(ws[1:]) == 0.0


def test_peak_wind_is_none_for_no_waypoints_at_all():
    assert peak_wind_kmph(()) is None


def test_peak_wind_ignores_an_unreported_zero():
    """The case that separates the two conditions, and the one IBTrACS produces.

    A blank `USA_WIND` is written as `0.0` by the source file, so a parser that
    flags it `wind_reported: false` can still be holding the number 0.0. If
    `peak_wind_kmph` filtered on "is the value non-zero" or on "is the value
    present" instead of on the flag, this fix would set the peak of a whole
    storm to zero — and 0 kmph feeds the surge law as 0.0 m, presented as a
    prediction that the storm does nothing.
    """
    unreported_zero = _waypoint(
        "2024-05-25T15:00:00Z", wind_kmph=0.0, wind_reported=False
    )
    ws = (_waypoint(wind_kmph=35.0, wind_reported=True), unreported_zero)
    assert peak_wind_kmph(ws) == 35.0
    # And with no reported wind anywhere, the answer is "nothing", not zero.
    assert peak_wind_kmph((unreported_zero,)) is None


# --------------------------------------------------------------------------
# Serialization — T2 and T7 both hand these objects to json.dumps
# --------------------------------------------------------------------------


def test_waypoint_to_dict_round_trips_through_json():
    d = _waypoint().to_dict()
    assert set(d) == {
        "iso_time",
        "latitude",
        "longitude",
        "wind_kmph",
        "wind_reported",
        "pressure_hpa",
        "nature",
    }
    assert json.loads(json.dumps(d)) == d


def test_record_to_dict_round_trips_through_json():
    """The catalogue payload and the /track payload are both this dict."""
    record = _record()
    d = record.to_dict()
    assert d["cyclone_id"] == "2024145N14087"
    assert d["peak_wind_kmph"] == 35.0
    assert d["limitation"], "a record with no limitation may not ship"

    reloaded = json.loads(json.dumps(d))
    assert reloaded["cyclone_id"] == record.cyclone_id
    assert reloaded["peak_wind_kmph"] == record.peak_wind_kmph
    # Waypoints come out as plain dicts, one level deep — no dataclass object
    # survives into a payload a client has to parse.
    assert reloaded["waypoints"] == [w.to_dict() for w in record.waypoints]
    assert all(isinstance(w, dict) for w in d["waypoints"])


def test_record_to_dict_keeps_the_field_names_the_record_declares():
    d = _record().to_dict()
    assert [f.name for f in dataclasses.fields(CycloneRecord)] == list(d)


# --------------------------------------------------------------------------
# LiveStatus
# --------------------------------------------------------------------------


def test_live_status_serialises_with_source_and_timestamp():
    # Every field a caller needs to judge staleness, even on failure.
    s = LiveStatus("live_unavailable", "nrlmry.navy.mil", 403,
                   "The source refused the request.", "2026-10-01T00:00:00Z",
                   ("https://www.nrlmry.navy.mil/atcf_web/docs/current_storms.txt",))
    d = s.to_dict()
    assert d["status"] == "live_unavailable"
    assert d["http_status"] == 403
    assert d["checked_at"] == "2026-10-01T00:00:00Z"
    assert d["endpoints"]


def test_live_status_values_are_closed_at_the_type_level():
    """Three states, not two, and a typo is a type error.

    `no_active_storm` ("the source answered; there is nothing active") and
    `live_unavailable` ("we could not get a trustworthy answer") are the same
    payload with different meanings. Merging them tells a judge a working feed
    was broken; keeping them open-ended lets T3 invent a fourth.
    """
    hints = typing.get_type_hints(LiveStatus)
    assert typing.get_origin(hints["status"]) is typing.Literal
    assert typing.get_args(hints["status"]) == (
        "available",
        "live_unavailable",
        "no_active_storm",
    )


def test_live_status_tolerates_an_unknown_http_status():
    """A DNS failure or a timeout has no HTTP status, and that is not an error.

    `None` is the honest value there, so `live_unavailable` can say what
    happened without inventing a status code nobody received.
    """
    s = LiveStatus("live_unavailable", "example", None, "Timed out.",
                   "2026-10-01T00:00:00Z", ())
    assert s.to_dict()["http_status"] is None


def test_live_unavailable_reason_names_the_limitation_and_promises_no_substitute():
    r = LIVE_UNAVAILABLE_REASON
    assert "live" in r.lower()
    assert "historical" in r.lower()   # says what it will NOT do
    assert "not" in r.lower()
    # The promise has to be a promise, not just the word "historical" appearing
    # somewhere in a paragraph about the catalogue: dropping the whole
    # "nothing is being substituted" clause must fail this.
    assert "substitut" in r.lower()
    # Plain language, for a judge reading a screen: no paths, no hostnames, no
    # stack-trace vocabulary, and no braces left over from a format string.
    assert "{" not in r and "}" not in r
    assert "/" not in r and "http" not in r.lower()
    assert len(r) < 400, "this is a sentence on a screen, not a specification"


# --------------------------------------------------------------------------
# The source protocol, and the frozen-ness of the two records
# --------------------------------------------------------------------------


def test_cyclone_source_declares_one_signature():
    """T2's `IbtracsSource` and T3's `AtcfLiveSource` both conform to this."""
    assert typing.get_type_hints(CycloneSource)["identifier"] is str
    assert inspect.iscoroutinefunction(CycloneSource.fetch)

    hints = typing.get_type_hints(CycloneSource.fetch)
    assert hints["cyclone_id"] == (str | None)
    assert hints["return"] == (CycloneRecord | None)

    params = inspect.signature(CycloneSource.fetch).parameters
    assert list(params) == ["self", "cyclone_id"]
    assert params["cyclone_id"].default is None


def test_a_duck_typed_source_satisfies_the_protocol():
    """Structural typing, not inheritance: the registry must accept both
    implementations without either importing the other."""
    class FakeSource:
        identifier = "fake"

        async def fetch(self, cyclone_id: str | None = None):
            return None

    assert isinstance(FakeSource(), CycloneSource)


def test_records_are_frozen():
    """A normalised record is a fact. Mutating one in place is how a cache
    starts serving a storm's track under another storm's key."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        _waypoint().latitude = 20.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _record().cyclone_id = "2024001N00000"  # type: ignore[misc]