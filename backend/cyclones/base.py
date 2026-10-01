"""The normalized cyclone record — the one shape every cyclone source speaks.

Two very different feeds become one object here. IBTrACS is a committed CSV of
best-track fixes, read offline, for storms that ended months or decades ago
(T2). ATCF is a plain-text bulletin served by a military meteorological
organisation, fetched over the network, for a storm that may be happening right
now (T3). Before this module neither of them existed in any reusable form: the
only track in the app was `/track`, a dict built for one case study and shaped
by what the mobile client happened to need.

Why normalise rather than pass dictionaries around
--------------------------------------------------
A dict has no required keys, so every consumer re-derives "what is the peak
wind" and one of them will do it wrong. `peak_wind_kmph` exists because the
naive version is wrong in both directions (below), and `CycloneRecord` exists
because a field that is always present can be checked once, here.

The three rules this module encodes, each from a bug already made once
--------------------------------------------------------------------
1. **A missing wind is not a zero wind.** IBTrACS writes a blank `USA_WIND` as
   `0.0`, so a fix can carry a number that means *not reported*. `main.py`'s
   `/track` serves a `wind_reported` flag, but it derives it as
   `float(wind_kt) > 0` — a test on the value, which is the exact rule this
   module exists to avoid, and it costs that endpoint every reported 0 kt.
   Here the flag is set by the *parser* that read the file, and
   `peak_wind_kmph` filters on it without ever consulting the value.
2. **Unavailable is not empty.** `LiveStatus.status` has three values, not two:
   `no_active_storm` means the source answered and there is nothing active,
   `live_unavailable` means no trustworthy answer arrived. They are the same
   payload and completely different messages to a user, and collapsing them is
   how a working feed gets reported as broken.
3. **Nothing is ever silently substituted.** When a live feed cannot be
   reached the app says so in `LIVE_UNAVAILABLE_REASON` and shows no storm.
   There is no code path from a live failure to a historical record, and this
   module deliberately provides no helper that could make one — the fallback
   would have to be written by whoever adds it, in the open.

What this module deliberately does not do
-----------------------------------------
- **It does not convert units.** Every wind here is kmph, but ATCF publishes
  knots and IBTrACS has both; the conversion belongs to the source that
  publishes the units, next to its own knowledge of what it is reading. An
  averaging-period disagreement is worse than a unit error and cannot be fixed
  by arithmetic — see `live.py`'s limitation string.
- **It does not validate.** A waypoint with a latitude outside [-90, 90] is
  either a hemispheric coordinate nobody converted (ATCF's `NS`/`EW` fields)
  or a parse bug, and either way the parse layer is where the message can name
  the line and column. A `ValueError` from here would say less and fire later.
- **It does not constrain `basin`.** IBTrACS identifies the basin by a
  character in the storm id (`2024145N14087`), ATCF by a two-letter code (`IO`).
  They are different vocabularies for the same water, and this module stores
  what the source published rather than inventing a third. `basin` and
  `subbasin` are therefore opaque strings here; filtering by basin is the
  source's job, and it owns its code.
- **It does not fetch anything.** No network, no filesystem, no Gemini. The
  fastest test in the repo is a test of this module for that reason.

Name collision, stated once: `CycloneRecord.peak_wind_kmph` is a *field* and
`peak_wind_kmph()` is the *function* that computes it from waypoints. They are
the same quantity and the field is not derived automatically — a dataclass
field that recomputed itself would have to re-read `waypoints` on every access
and would silently disagree with the value a source chose to publish. Fill the
field from the function; where they disagree, the field is what the source
claimed and the function is what its own waypoints support, and that gap is
worth surfacing rather than resolving here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Literal, Protocol, runtime_checkable

#: Shown to a user when no live cyclone feed could be reached. This string
#: reaches a judge's screen, so it is written for someone who has never seen a
#: best-track file.
#:
#: Five things it has to do, and why each is not optional:
#:
#: - *Say the feed is down.* Silence is indistinguishable from "no storm".
#: - *Say nothing is being shown.* The blank map would otherwise read as "this
#:   storm has no track".
#: - *Promise no substitution.* The single most damaging thing this app could
#:   do is show Remal's May 2024 track under a "live cyclone" label and let it
#:   pass unremarked. So the promise is in the sentence, not in a changelog.
#: - *Say what still works.* The historical list and the deterministic
#:   simulation are independent of this feed, and a reader who thinks the whole
#:   app is broken is being told something false.
#: - *Point at the time of the attempt.* The sentence above deliberately carries
#:   no timestamp, because a module-level constant has no runtime clock and
#:   calling one would put a fabricated time in a string. `live_unavailable_reason()`
#:   below appends the real one, so use that rather than this constant directly:
#:   the sentence and its timestamp are one message, and the brief's two
#:   requirements — a pinned constant, and a reason that names when the attempt
#:   happened — are satisfied by composing rather than by choosing one.
#:
#: Deliberately absent: hostnames, file paths, HTTP status codes, exception
#: names. Those belong in `LiveStatus.source` / `.http_status` / `.endpoints`,
#: which carry them for whoever is debugging — not in the sentence a judge reads.
LIVE_UNAVAILABLE_REASON = (
    "The live cyclone feed could not be reached, so no live cyclone is being "
    "shown. No historical or case-study cyclone is being substituted for live "
    "data. The historical cyclone list and the deterministic storm-surge "
    "simulation are unaffected. The time of this attempt is reported with this "
    "message."
)


def live_unavailable_reason(checked_at: str) -> str:
    """`LIVE_UNAVAILABLE_REASON` with the time of the attempt appended.

    This is the only sanctioned way to build the `reason` of a `live_unavailable`
    status, and it exists because the two halves of the judge-facing message
    cannot live in one place. The promise — feed down, nothing substituted,
    history unaffected — belongs in a pinned constant so it cannot be edited
    per-source. The time of the attempt is a runtime fact, so it has to be
    interpolated; and a constant that claimed a timestamp would be a fabricated
    one. Composing keeps the sentence and its timestamp inseparable, which is
    the property that matters: a reason with no time cannot be judged stale, and
    a time with no promise does not say what is being shown instead.

    T3's `probe()` also has diagnostic detail worth showing — "tried 4 sources:
    403, 403, 404, 200" — and may legitimately prepend or follow that with this.
    What it must not do is ship a `live_unavailable` reason that omits the
    promise; the composition is the floor, not the ceiling.

    `checked_at` is the same string as `LiveStatus.checked_at`. Empty is
    rejected rather than rendered, because an empty timestamp prints as
    "Attempt made at ." — a message that looks complete and says nothing.
    """
    if not checked_at or not checked_at.strip():
        raise ValueError(
            "checked_at must be a non-empty UTC timestamp such as "
            f"'2026-10-01T00:00:00Z'; got {checked_at!r}. An empty value would "
            'render as "Attempt made at ." — a sentence that looks complete and '
            "carries no time."
        )
    return f"{LIVE_UNAVAILABLE_REASON} Attempt made at {checked_at}."


@dataclass(frozen=True)
class CycloneWaypoint:
    """One position along a cyclone's track — a best-track fix or an ATCF fix.

    Frozen because a normalised record is a fact: a waypoint mutated after a
    cache was keyed is exactly how a cache starts serving one storm's track
    under another storm's id.

    `iso_time` is a UTC **string**, not a `datetime`. The sources disagree on
    timezone and precision — IBTrACS carries unzoned UTC to the minute, ATCF to
    the second — and normalising to `datetime` here would push that decision
    into every parser and make it invisible at the point it is made.

    **One canonical rule, in two layers.**

    *At rest* the spelling is IBTrACS's own `ISO_TIME`: `YYYY-MM-DD HH:MM:SS`,
    UTC, **no `Z`** (`2024-05-25 12:00:00`). Both providers conform —
    `atcf._timestamp_to_iso` turns ATCF's `YYYYMMDDHH` into exactly this rather
    than inventing a third format — so the catalogue, the committed GeoJSON and
    any two sources can be compared as plain strings, and they sort correctly.

    *On the wire* the spelling is RFC 3339: `2024-05-25T12:00:00Z`. Converted in
    exactly one place, `iso_time_to_rfc3339()` below, called only at the API
    boundary. Every timestamp field a client sees is RFC 3339 — `/track`,
    `/cyclones/{id}/track` and `/live-cyclone` agree — because two spellings of
    one field name is a client parsing it one way against one endpoint and
    another way against the next.
    """

    iso_time: str
    latitude: float
    longitude: float
    #: Sustained wind in kmph, or `None` when the source published none.
    #: **Never a substitute value**: see the module docstring, rule 1.
    wind_kmph: float | None
    #: Whether the source actually reported a wind at this fix. This is the
    #: flag every filter must use, because IBTrACS encodes "not reported" as
    #: `0.0` and a reported 0 kt is a real observation.
    wind_reported: bool
    pressure_hpa: float | None
    #: The source's own classification at this fix — IMD ("TS", "D"), JTWC, or
    #: ATCF's storm-object code. Kept verbatim and untranslated: two sources
    #: publish two schemes, and a mapping that silently preferred one would let
    #: a caller believe a label means something it does not.
    nature: str | None

    def to_dict(self) -> dict:
        """Plain, JSON-serializable dict of the seven fields. T2 and T7 both
        serialize waypoints; neither should re-derive this."""
        return asdict(self)


def iso_time_to_rfc3339(iso_time: str) -> str:
    """The at-rest spelling -> RFC 3339 (`...Z`), or raise.

    **The one converter.** `main._parse_track_timestamp` and `main._iso_z` (the
    track endpoints) and `AtcfLiveSource` (the `/live-cyclone` payload) all call
    this, so the wire format cannot drift between endpoints the way
    `data/remal_track.geojson` once drifted from `data/cyclones/catalogue.json`
    — two files describing one storm 11.1 kmph apart.

    It raises rather than passing a malformed instant through. A timestamp that
    is not in the at-rest form is a source that changed its format, and serving
    it verbatim would put a second spelling on the wire, which is the defect
    this function exists to make impossible.
    """
    text = str(iso_time).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError(
            f"iso_time {iso_time!r} is not the at-rest form '%Y-%m-%d %H:%M:%S'"
        ) from exc
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class CycloneRecord:
    """One cyclone, from any source, normalized.

    `cyclone_id` is the cache key for everything downstream (T5 re-keys the
    simulation caches on it), so it must be unique across sources rather than
    merely unique within one of them — `live.py` prefixes ATCF identifiers for
    that reason.

    `observed` is not "real" as opposed to "simulated": both records describe
    what a source actually published. It marks the record as an observation of
    a storm that existed, as opposed to a scenario the user dialled in, which is
    what lets a scenario named `observed` be distinguished from one named after
    an IMD band that happens to resolve to the same wind.

    `fetched_at` and `data_through` are different facts and the distinction is
    the whole point of keeping both. `fetched_at` is when this process read the
    data; `data_through` is the timestamp of the *last fix in the file*, which
    for a historical storm is months in the past and for a live storm is
    minutes. A client that shows only `fetched_at` will claim a 2011 cyclone was
    updated a second ago.

    `limitation` is mandatory and has no default. A record with an empty
    limitation would put "must state its provenance" on every caller's honour,
    and honour is what fails under deadline.
    """

    cyclone_id: str
    name: str
    season: int
    basin: str
    subbasin: str | None
    #: Identifier of the `CycloneSource` that produced this ("ibtracs_v04r01_ni",
    #: "atcf_live"). Names the reading method, the way `SURGE_METHOD` does for a
    #: surge number.
    source: str
    observed: bool
    waypoints: tuple[CycloneWaypoint, ...]
    fetched_at: str
    data_through: str | None
    #: Finite, or `None`. Expected to be `peak_wind_kmph(record.waypoints)`;
    #: see that function for why a non-finite value must not survive here.
    peak_wind_kmph: float | None
    limitation: str

    def to_dict(self) -> dict:
        """Plain, JSON-serializable dict. Waypoints become plain dicts one level
        deep — no dataclass instance survives into a payload a client parses."""
        return asdict(self)


def peak_wind_kmph(waypoints: Iterable[CycloneWaypoint]) -> float | None:
    """The strongest wind any fix actually reported, in kmph, or `None`.

    A fix contributes only when `wind_reported` is true, `wind_kmph` is not
    `None`, and that value is finite. `None` means "we have measured nothing",
    and returning it is honest; returning `0.0` would feed `0.0` into
    `surge_for_wind` and produce a surge of 0 m presented as a prediction.

    A reported `0.0` does contribute, and that asymmetry is deliberate: a
    best-track agency does report 0 kt for a storm that has dissipated, and
    dropping those would understate the storm's life. Both directions are tested
    in `tests/test_cyclones_base.py`.

    Non-finite winds are excluded, and this is not an input-validation
    niceness — it is a correctness fix. `max()` is order-dependent around NaN:
    `max(nan, 35.0)` is 35.0 but `max(35.0, nan)` is nan, so a single poisoned
    fix silently sets the peak of a whole storm to NaN *or* hides behind it,
    depending on track order. From there `surge_for_wind(nan)` returns nan, and
    `json.dumps` writes a bare `NaN` token, which is **not JSON** — `json.loads`
    accepts it only because Python is lenient by default and a conforming
    parser will not. `float("inf")` is the same class of problem: it would read
    as the most extreme cyclone ever recorded. The twin of this function,
    `peakReportedWindKmph` in `mobile/trackFacts.ts`, already guards with
    `Number.isFinite`; this is that guard, in Python.

    The guard is a backstop, not a licence. A non-finite wind must never reach
    this function: IBTrACS coerces with `float()` and never range-checks, and
    `json.loads` will happily read a bare `NaN` back out of a file. So the
    parsers in T2 and T3 own the rejection, where they can name the row and the
    column they were reading.
    """
    reported = (
        w.wind_kmph
        for w in waypoints
        if w.wind_reported
        and w.wind_kmph is not None
        and math.isfinite(w.wind_kmph)
    )
    return max(reported, default=None)


@dataclass(frozen=True)
class LiveStatus:
    """Whether a live cyclone feed answered, and how.

    Frozen for the same reason the records are, plus one more: this object is
    cached and shown, so a status that mutates after being served is a screen
    claiming a feed is up when the last probe said otherwise.

    The three states, and why the unhappy ones are not one state:

    - `available` — a storm was parsed. `cyclone` is somewhere in the response.
    - `no_active_storm` — the source answered (200) and listed no NI cyclone.
      The feed works. There is simply nothing to show, which is a completely
      different thing from the feed being broken and is the normal state of the
      app for most of the year.
    - `live_unavailable` — no trustworthy answer: refused, timed out, DNS
      failure, unparseable body. Nothing is shown in its place.

    `http_status` is `int | None` because a timeout and a DNS failure never
    received a status code, and inventing one would be a fabricated observation.
    `endpoints` lists what was tried in order even when all of it failed — that
    is the evidence behind `reason`, and the reason a probe that tried four
    sources reports four sources.

    `status` is a `Literal`, closed at three values. A fourth state invented by
    a later task is then a type error rather than a string that reaches a client
    unlabelled.

    `reason` carries the rule a judge-facing screen depends on: **a
    `live_unavailable` status must state that nothing is being substituted for
    the live feed.** Build it with `live_unavailable_reason(checked_at)`, which
    is the sentence plus the time of the attempt; a probe that also wants to
    report what it tried can add that detail, but not instead of the promise.
    The other two states have no such obligation — `no_active_storm` is a
    working feed with nothing to report, and `available` has a storm.
    """

    status: Literal["available", "live_unavailable", "no_active_storm"]
    source: str
    http_status: int | None
    reason: str
    #: When the probe ran, UTC, ending in `Z`. Present on every state including
    #: both failures, because a stale failure reported as current is its own
    #: kind of lie. `LIVE_UNAVAILABLE_REASON` refers the reader to this.
    checked_at: str
    endpoints: tuple[str, ...]
    #: The storm itself, when there is one. `None` on every other state — and
    #: `None` is the whole point: a live feed that answered with nothing, or did
    #: not answer at all, must not have a historical cyclone placed in this
    #: field by a caller who found one convenient.
    #:
    #: A summary dict rather than a `CycloneRecord`, so this module stays free of
    #: the live parser and the two never have to agree on a shape.
    cyclone: dict | None = None

    def to_dict(self) -> dict:
        """The seven fields, plain. `GET /live-cyclone` returns exactly this plus
        `limitation`, so these keys must not be renamed."""
        return asdict(self)


@runtime_checkable
class CycloneSource(Protocol):
    """Anything that can produce a `CycloneRecord` for this app.

    Structural, not inherited: `IbtracsSource` (T2) and `AtcfLiveSource` (T3)
    share nothing but these two members, and neither should import the other or
    know which of them a caller meant.

    `fetch` is `async` because one of the two implementations performs a network
    request. The historical one does no I/O beyond reading a committed file, but
    giving it an `async def` that returns immediately is cheaper than two
    signatures the registry has to branch on — and a registry that awaits both
    is the shape T5 needs.

    `cyclone_id=None` means "whatever storm you have". A source with one
    recorded storm returns it; a live source returns the active one. A specific
    id means that storm. Returning `None` means "I could not get it", never "I
    substituted something else" — see `LIVE_UNAVAILABLE_REASON`.

    `@runtime_checkable` so a registry holding a list of sources can check what
    it was handed. Be aware of what that check buys: it verifies the attributes
    exist, not that they behave, so it is a routing guard, not a test — which
    is why every implementation still needs its own tests.
    """

    identifier: str

    async def fetch(self, cyclone_id: str | None = None) -> CycloneRecord | None:
        """Return the record, or `None` if it could not be obtained."""
        ...
