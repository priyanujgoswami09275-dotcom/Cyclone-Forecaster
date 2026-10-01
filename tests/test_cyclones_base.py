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
   them into zeros would invent a calm. It also rejects non-finite winds, because
   `max()` is order-dependent around NaN and `json.dumps` would write a bare
   `NaN` token that no conforming parser accepts. Both directions are tested
   (MEMORY.md §31, and `peakReportedWindKmph` in `mobile/trackFacts.ts`, which is
   the twin of this function on the client).
2. **`LiveStatus` has three states, not two.** `no_active_storm` means the
   source answered and there is nothing active; `live_unavailable` means we could
   not get a trustworthy answer. Collapsing them would tell a judge that a
   working feed was broken. The set is closed at the type level.
3. **`LIVE_UNAVAILABLE_REASON` promises no substitute.** This is the sentence
   shown when no live feed can be reached. It has to say the feed is down, that
   nothing is standing in for it, and that the historical list and the
   deterministic simulation are untouched — because a reader who suspects the
   app quietly fell back to Remal would be right to be suspicious of it.
   `live_unavailable_reason()` is how that sentence reaches a status with the
   time of the attempt attached, so the promise is reachable through the payload
   rather than floating as an unused constant.
4. **The declared shapes are contracts, written out as literals.** T2 serves a
   catalogue from `CycloneRecord.to_dict()`, T7 serves `/live-cyclone` from
   `LiveStatus.to_dict()`, and T9 types the client off both. So the expected key
   lists below are hardcoded rather than read back from `dataclasses.fields()`:
   a test that compares a declaration to itself cannot fail, and a renamed field
   is a silent `undefined` in a client instead of a failure here.
5. **The source protocol has exactly one signature.** T3's `fetch` and T2's
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
    live_unavailable_reason,
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


#: The record's field values, module-level so a test can drop one and prove the
#: constructor rejects it. `_record()` copies it; nothing mutates it.
_RECORD_FIELDS = {
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


def _record(**overrides) -> CycloneRecord:
    """A minimal two-fix record; overrides let a test state only what differs."""
    fields = dict(_RECORD_FIELDS)
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


def test_peak_wind_ignores_a_non_finite_wind():
    """A NaN must not be able to poison the maximum, in either track order.

    `max()` is order-dependent around NaN — `max(nan, 35.0)` is 35.0 but
    `max(35.0, nan)` is nan — so a single poisoned fix either sets a whole
    storm's peak to NaN or hides behind the real peak, depending on where the
    source happened to put it in the file. From there `surge_for_wind(nan)`
    returns nan, and `json.dumps` writes a bare `NaN` token: not JSON. Any
    conforming client parser rejects the response.

    `peakReportedWindKmph` in `mobile/trackFacts.ts` already guards with
    `Number.isFinite`; this is the same guard, in Python. The parser in T2/T3
    owns rejecting it at the row, but the peak must not depend on it.
    """
    nan_fix = _waypoint(wind_kmph=float("nan"), wind_reported=True)
    real = _waypoint("2024-05-25T15:00:00Z", wind_kmph=35.0, wind_reported=True)
    assert peak_wind_kmph((real, nan_fix)) == 35.0
    assert peak_wind_kmph((nan_fix, real)) == 35.0
    # Alone, a NaN is "no measurement", not a measurement.
    assert peak_wind_kmph((nan_fix,)) is None

    # Infinity is the same class of poison: it would read as the most extreme
    # cyclone ever recorded, and `surge_for_wind(inf)` is inf metres of surge.
    inf_fix = _waypoint(wind_kmph=float("inf"), wind_reported=True)
    neg_inf_fix = _waypoint(wind_kmph=float("-inf"), wind_reported=True)
    assert peak_wind_kmph((inf_fix,)) is None
    assert peak_wind_kmph((neg_inf_fix,)) is None
    assert peak_wind_kmph((real, inf_fix)) == 35.0

    # The consequence, stated concretely: this is what the guard prevents from
    # reaching a client. Python's json writes NaN because it is lenient by
    # default; `allow_nan=False` is what a conforming writer does.
    assert "NaN" in json.dumps({"peak_wind_kmph": float("nan")})
    with pytest.raises(ValueError):
        json.dumps({"peak_wind_kmph": float("nan")}, allow_nan=False)


# --------------------------------------------------------------------------
# Serialization — T2 and T7 both hand these objects to json.dumps
# --------------------------------------------------------------------------


def test_waypoint_to_dict_round_trips_through_json():
    d = _waypoint().to_dict()
    # Hardcoded, and in order: the brief constructs waypoints positionally
    # (`CycloneWaypoint("...", 19.2, 89.2, 35.0, True, None, "TS")`), so a
    # reorder is a break, not a cosmetic change. Written out rather than derived
    # from `dataclasses.fields()` — comparing the class against itself proves
    # nothing, it just cannot fail.
    assert list(d) == [
        "iso_time",
        "latitude",
        "longitude",
        "wind_kmph",
        "wind_reported",
        "pressure_hpa",
        "nature",
    ]
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
    """The 12 keys, hardcoded and in the brief's order.

    Nine later tasks consume this dict — T2 builds the whole catalogue from it,
    T7 serves `/cyclones` and `/cyclones/{id}/track` from it. A rename is a
    breaking change to a response contract, and this test is where that rename
    gets caught instead of a `KeyError` six call sites away.

    `dataclasses.fields()` is deliberately NOT the source of the expected list.
    Comparing the declaration against itself cannot fail — a renamed field just
    renames on both sides — so the earlier version of this test proved nothing.
    """
    assert list(_record().to_dict()) == [
        "cyclone_id",
        "name",
        "season",
        "basin",
        "subbasin",
        "source",
        "observed",
        "waypoints",
        "fetched_at",
        "data_through",
        "peak_wind_kmph",
        "limitation",
    ]


# --------------------------------------------------------------------------
# The caveat that may not be defaulted away
# --------------------------------------------------------------------------


def test_limitation_is_mandatory_and_cannot_be_defaulted_away():
    """No default. Ever.

    This is the whole enforcement of "a screening estimate must never read as a
    forecast" on this record. A `limitation: str = ""` default would let every
    caller forget it, and a forgotten caveat is indistinguishable from no caveat
    on a screen — so the declaration is what gets asserted here, not a value. A
    test asserting `d["limitation"]` proves nothing: a fixture compares its own
    literal to itself and passes for a defaulted field just as happily.
    """
    assert dataclasses.fields(CycloneRecord)[-1].name == "limitation"
    assert dataclasses.fields(CycloneRecord)[-1].default is dataclasses.MISSING

    # Stronger than the one field: *nothing* on the record may be defaulted, so
    # a later task cannot make a constructor call shorter by adding one.
    defaulted = [
        f.name
        for f in dataclasses.fields(CycloneRecord)
        if f.default is not dataclasses.MISSING
    ]
    assert defaulted == []

    # And behaviourally, not just structurally: a record without a limitation
    # cannot be constructed at all.
    without = dict(_RECORD_FIELDS)
    without.pop("limitation")
    with pytest.raises(TypeError, match="limitation"):
        CycloneRecord(**without)


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


def test_live_status_to_dict_keys_are_the_endpoint_response_contract():
    """The six keys, hardcoded.

    T7 serves `GET /live-cyclone` from this dict verbatim, and T9's
    `apiCyclones.ts` types the response from it. So these six names *are* an API
    contract, and a renamed key is a client that silently reads `undefined`.
    Hardcoded rather than compared against `dataclasses.fields(LiveStatus)`, for
    the same reason as the record test above: the declaration cannot disagree
    with itself.
    """
    s = LiveStatus("no_active_storm", "example", 200, "Feed answered; no NI "
                   "cyclone listed.", "2026-10-01T00:00:00Z", ("https://x/y",))
    assert list(s.to_dict()) == [
        "status",
        "source",
        "http_status",
        "reason",
        "checked_at",
        "endpoints",
    ]


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


def test_live_unavailable_reason_composes_the_constant_with_the_time_of_the_attempt():
    """The constant alone is not the message. Composed, it is.

    The brief wants two things that a single constant cannot both be: pinned
    wording nobody edits per-source, and a reason that names when the attempt
    happened. `live_unavailable_reason()` is how both hold — the pinned sentence
    plus a real timestamp, never a fabricated one baked into a module-level
    string.
    """
    composed = live_unavailable_reason("2026-10-01T00:00:00Z")
    # The promise survives verbatim — that is the whole point of composing
    # rather than reformatting.
    assert LIVE_UNAVAILABLE_REASON in composed
    assert "substitut" in composed.lower()
    # And so does the time.
    assert "2026-10-01T00:00:00Z" in composed
    assert composed.startswith(LIVE_UNAVAILABLE_REASON)


def test_a_live_unavailable_status_can_carry_the_composed_reason():
    """The guarantee is reachable through the payload, not just the constant.

    A free-floating constant is a convention; this is the path T3's `probe()`
    takes. The diagnostic detail a probe has ("tried 4 sources: 403, ...") may
    go with it — what may not replace it is the promise.
    """
    checked_at = "2026-10-01T00:00:00Z"
    s = LiveStatus(
        "live_unavailable",
        "nrlmry.navy.mil",
        403,
        live_unavailable_reason(checked_at),
        checked_at,
        ("https://www.nrlmry.navy.mil/atcf_web/docs/current_storms.txt",),
    )
    d = s.to_dict()
    assert d["status"] == "live_unavailable"
    assert LIVE_UNAVAILABLE_REASON in d["reason"]
    assert d["checked_at"] in d["reason"]
    # And it survives the wire, because that is where it is read.
    assert LIVE_UNAVAILABLE_REASON in json.loads(json.dumps(d))["reason"]


def test_live_unavailable_reason_refuses_an_empty_timestamp():
    """An empty timestamp renders as "Attempt made at ." — complete-looking and
    empty. Refused at the point of composition, where the caller can be named."""
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="checked_at"):
            live_unavailable_reason(bad)


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
