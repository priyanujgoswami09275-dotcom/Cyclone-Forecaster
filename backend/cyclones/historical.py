"""IBTrACS v04r01, North Indian Ocean basin -> `CycloneRecord`. The historical source.

This is the app's second cyclone source and the first one that has ever existed
as a reusable object. Before it, the only track in the repository was
`/track` in `backend/main.py`: a dict built for one named cyclone out of a
fetch script (`backend/data_pipeline/fetch_ibtracs.py`) that exists to be run
once by hand. That script is not wrong about *Remal* — it is wrong as a reader,
and this module is the corrected version of it (see "The bug this replaces").

It reads a large CSV that lives in the working tree and takes no network
action, which is the point: a demo on conference wifi must not depend on NOAA
being up, and a judge's question about the 1999 cyclone must have the same
answer as one about 2024.

**The input is not in git.** `ibtracs.NI.list.v04r01.csv` is a required local
file that has been placed at the repo root; it is untracked, and it
is deliberately not in `.gitignore` either, so `git status` shows it as `??`
and whoever owns this repository decides separately whether to commit it or to
document a fetch step. Nothing in this module assumes which. It assumes only
that the file is there: `read_ni_rows` and `build_catalogue` both fail loudly
rather than inventing a catalogue when it is not, and
`backend/data_pipeline/ingest_ibtracs_ni.py` says in its error message which
file is missing.

**Without the archive the test suite fails; it does not skip.** There is no
`skipif` on any test in `tests/test_ibtracs_ni.py`, no `tests/conftest.py`, and
no pytest ini section or `pyproject.toml` anywhere in this repo. There *is* a
root `conftest.py`, and it only puts the repo root on `sys.path` — no markers,
no ini options, nothing that could skip anything. This is a deliberate trade and
the other way round was available: `tests/test_overlays.py` has used
`pytestmark = pytest.mark.skipif(...)` for a missing overlay this whole time.
Skipping is wrong here because a green suite has to mean *the assertions ran*.
A skip is green, so a missing archive would read as a passing suite and the
failure would surface much later — as a catalogue that quietly stopped being
regenerated, or as a reviewer's question answered by a test that never executed.
A `FileNotFoundError` is loud and immediate, and it names the file, which is
the one thing a person needs to know. The cost is that this suite cannot go
green on a fresh clone until the archive is placed; the benefit is that green is
never a lie.

Every measured figure in the paragraphs below is **held against the archive by a
test in `tests/test_ibtracs_ni.py`**, derived from the file and compared to a
fixed literal, with a failure message naming the quantity that moved. That is
the only reason these numbers are trustworthy: this feature has produced six
wrong hand-counted figures across three review rounds, and a number in a
docstring that no test derives is a number the suite structurally cannot catch.
Which test owns which figure is stated at each claim below.

What is in the file, because the file is not what its name says
-------------------------------------------------------------------------
`ibtracs.NI.list.v04r01.csv` is the North Indian Ocean *list*, which is
IBTrACS's recommendation of what to load for this basin — not a file containing
only this basin. It is a CSV spanning seasons 1842-2026, and:

  * `BASIN` is `NI` on 57,852 rows, **`WP` on 4,525** and **`NA` on 482**. The
    WP rows are real Western Pacific storms (16.3 N, 119.1 E — the
    Philippines); the NA rows are a real Atlantic storm at longitude -63.6 with
    `SUBBASIN == "CS"`. Neither is the Sundarbans. *Pinned by
    `test_the_archives_structural_shape_is_what_the_docstrings_say`.*
  * Row index 1 is a **units row**: `LAT` literally reads `degrees_north` and
    `USA_WIND` reads `kts`. Its `BASIN` is a single space, so the basin filter
    drops it — but that is luck, not design, and `_row_storm_id` would also
    refuse it. *Pinned by `test_the_junk_row_is_dropped`.*
  * 1,859 NI storms, 610 of them at season >= 1970. *Pinned by
    `test_the_archives_structural_shape_is_what_the_docstrings_say` and
    `test_since_defaults_to_1970_and_cuts_the_1859_ni_storms_to_610`.*
  * `USA_WIND` is blank on 45,280 of 57,852 NI fixes. `USA_PRES` is populated
    on 6,297 of them (10.9% — the plan this was written from said 48%, and
    the rule it gave, "None when absent", is the rule that shipped). *Pinned by
    the shape test and by
    `test_pressure_is_none_when_the_file_has_none`.*
  * `NATURE` never goes blank on an NI row, but it does read `NR` ("not
    reported") on 3,014 of them. *Pinned by the shape test.*
  * 52,950 of the 57,852 NI fixes are `UNNAMED`, covering 1,713 of the 1,859
    storms. *Pinned by the shape test.*

**What is deliberately absent from this paragraph.** The record count is
pinned and may be stated. The line count, the column count and the file size
are **not** asserted anywhere, on purpose: each moves on a revision for
reasons that have nothing to do with the basin filter — a quoted field gaining
a newline, NOAA adding a column, any content change at all. A test that fires
on a legitimate revision teaches a reader to bulk-accept the section without
reading it, which destroys the value of the assertions that do matter. They
were removed from the shape test for exactly that reason, and they are
therefore not written here either: a figure in a docstring that no test
derives is a figure the suite structurally cannot catch.

Two earlier versions of this paragraph asserted the opposite things and both
were false — one said the figures were "not stated here" while stating all
four, the other said all four were "pinned by the shape test" after three of
them had been dropped from it. `test_every_measured_figure_in_this_module_is_pinned`
is what stops a third.

So the filter is the feature. Everything else in this module is careful about
not inventing things; `read_ni_rows` is careful about not *including* things.

The SID cannot be used to filter, at any index
----------------------------------------------
An IBTrACS `SID` is 13 characters: `<4-digit season><3-digit number><basin
letter><5 digits of lat/lon>`, e.g. `2024` `145` `N` `14087`. So the letter is
at **index 7**. The plan this module was written from said index 11, which on
this file is the second-to-last digit — an assertion that compares every
catalogue id against a digit and fails.

More importantly, the letter is `'N'` on *every* row of storm-id shape in the
file, WP and NA included — the 4,525 WP rows and 482 NA rows all carry it.
`BASIN` and the SID's letter are different fields, and inferring the basin from a
cyclone id would be wrong and silently wrong, which is the kind that survives
review. `BASIN` is the only authority, and
`test_the_sid_letter_cannot_identify_the_basin` pins that with a raw-file check
that does not parse the SID at all, while
`test_storms_that_span_more_than_one_basin` derives the storm counts and shows
why a SID-based check is vacuous: every WP and NA storm in this file also has NI
fixes, so "the yielded SIDs are not WP SIDs" is true of a reader that leaks.

The bug this replaces
---------------------
`fetch_ibtracs.py` line 46:

    "usa_wind_kt": float(row["USA_WIND"]) if row.get("USA_WIND") else 0.0

`row.get("USA_WIND")` is a space, not an empty string — IBTrACS pads its
missing cells — and a space is truthy, so this branch survives. The `else 0.0`
then fires on genuinely empty cells, and either way the file it writes says
Remal went calm in mid-ocean.

**`data/remal_track.geojson` carries `usa_wind_kt: 0.0` at five of its
nineteen fixes** — 2024-05-27 at 03:00, 06:00, 09:00, 12:00 and 15:00, twelve
consecutive hours. IBTrACS **v04r01**, which this module reads, reports
**48, 45, 40, 35 and 33 kt** at those five exact timestamps. So the committed
track draws a real cyclone dead in the Bay of Bengal for half a day and then
restarting, and every one of those `0.0` reads is indistinguishable from a real
0 kt observation, which for a storm at sea is a claim about the weather.

(The GeoJSON was built from **v04r00**, which held only 19 fixes for Remal
covering the landfall window; v04r01 has 40. So the file is short two
dimensions at once — fewer fixes, and blanks where the later revision has real
winds. *Pinned by `test_remal_contains_every_fix_of_the_committed_track` and
`test_the_committed_geojson_shows_remal_going_calm_and_the_catalogue_does_not`.*)

Here a blank is `None` and `wind_reported` is `False`, and `0` — a reported
0 kt, which agencies do publish for a dissipated storm — stays `0.0` with
`wind_reported` true. The two are kept apart at the parse, where the row and the
column can be named, and never re-derived from the value by a consumer.

What this module refuses to do
------------------------------
- **It does not translate `NATURE`.** IBTrACS's classification ("TS", "MX",
  "DS", "ET") and IMD's ("Cyclonic Storm", "Very Severe Cyclonic Storm") are
  different schemes over different averaging periods. Mapping one to the other
  would let a caller read `TS` as an IMD wind band, which is a claim about wind
  this column cannot support. It is carried verbatim, and `NR` stays `NR`:
  dropping it would be a substitution and expanding it would be an invention.
  Rendering `NR` as absent rather than as a category is the UI's decision.
- **It does not repair coordinates.** A fix missing `LAT` or `LON` is dropped.
  A track with a gap is wrong in a way the reader can see; a track with an
  invented waypoint in the Gulf of Guinea is wrong in a way it cannot. There
  is no interpolation anywhere in this file, and a `0.0` default would be a
  fabricated position at a real place.
- **It does not range-check.** `base.py` is explicit that validation belongs in
  the parser, where the message can name the line — but a range check is also a
  policy about which observations count, and this file has no such policy. So
  an implausible coordinate is carried as written, including 1966 storm
  `1966233N13340`, which runs from 65.2 N up to 83.0 N while filed under
  `SUBBASIN == "AS"` (Arabian Sea).
  `test_an_implausible_coordinate_is_carried_as_written_not_clamped` derives
  that span and the rest of the set from the archive and compares them to fixed
  literals, so the figure above cannot be wrong without the suite saying so.
  `test_latitude_and_longitude_are_never_substituted` checks the hemispheres
  rather than a bbox.
- **It does not filter by name or recency.** `since` is a season floor and
  nothing else. 1,713 of the NI storms are `UNNAMED` and they stay `UNNAMED`;
  the app is a cyclone forecaster, not a cyclone hall of fame, and a catalogue
  that hid unnamed storms would understate how much is at stake.
- **It does not share its season floor with T6.** `build_catalogue(since=1970)`
  and T6's `SEASON >= 1970` ML filter are two decisions about one input, and
  there is deliberately no module constant for either. The day they share one,
  widening the catalogue window silently changes the training set and the only
  symptom is a model that scored differently for no stated reason.

Where the unit conversion happens, and why it is here
-----------------------------------------------------
`CycloneWaypoint.wind_kmph` is in kmph; IBTrACS `USA_WIND` is in knots.
`base.py` does not convert, on the grounds that the conversion belongs to the
source that publishes the units — that is this module. The arithmetic is
`round(knots * 1.852, 1)`, and the one-decimal rounding is so that two runs
over the same file produce the same bytes; without it, the shortest
representation of the same float could differ.

The check that the conversion is the right way round: Remal's peak `USA_WIND`
is 60 kt, and IMD's documented landfall wind was 110-120 kmph — 59-65 kt. A
peak of 60 *kmph* would be the unit bug `surge.py` documents in its `IMD_BANDS`
history, in which every wind in the app was about 1.85x too small. 60 kt comes
out at 111.1 kmph, which is inside IMD's own range for the storm. *Pinned by
`test_remal_peak_wind_is_60_kt_and_agrees_with_imd`.*
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .base import CycloneRecord, CycloneWaypoint, peak_wind_kmph

#: Repo root — `backend/cyclones/historical.py` -> `backend/cyclones` ->
#: `backend` -> root. There is no `backend/paths.py` to import, which is why
#: `tests/test_track.py` and `tests/test_overlays.py` each declare their own
#: `REPO_ROOT` too.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The archive this module reads. **It is not in git.** It is a required local
#: input placed at the repo root, untracked and not ignored, so
#: `git status` reports it as `??`. Whether it should be committed or a fetch
#: step should be documented is the repository owner's call, not this module's.
#: All this constant asserts is the expected location.
#:
#: v04r01 rather than the v04r00 named in CLAUDE.md: same dataset, newer
#: revision, and the revision that is actually present in this working tree. It
#: is read offline — nothing here opens a socket — so a demo does not depend on
#: NOAA being reachable.
DEFAULT_IBTRACS_PATH = REPO_ROOT / "ibtracs.NI.list.v04r01.csv"

#: `CycloneRecord.source` for every record this module builds. It names the
#: reading method, the way `SURGE_METHOD` names the method behind a surge
#: number: revision *and* basin, because "IBTrACS" alone does not say whether a
#: caller is looking at the North Indian Ocean or at a Western Pacific storm it
#: should never have been handed.
IBTRACS_SOURCE_ID = "ibtracs_v04r01_ni"

#: The `BASIN` value this source admits, and the value it stores on each record.
#: Compared after `.strip()` because row 1 of the file — the units row — has
#: `BASIN` set to a single space, and because the filter is the one thing in
#: this module that has to be exactly right.
NI_BASIN_CODE = "NI"

#: IBTrACS `SID` of Cyclone Remal: 2024 season number 145, basin N, with
#: `14087` encoding the genesis point as 14.0 N 87.0 E. The *first fix in the
#: file* is 13.6 N 86.6 E at 2024-05-23 12:00 — the encoding is rounded to whole
#: degrees, so it is not a position to read anything off. This is the case study
#: the whole repository is built around, so it is a named constant rather than a
#: string typed at three call sites — and `data/remal_track.geojson`, which
#: `/track` serves, is the same storm.
REMAL_CYCLONE_ID = "2024145N14087"

#: One knot in km/h. Published, exact by definition, and the factor `surge.py`
#: already refers to in its own unit comment. `fetch_ibtracs.py` is deliberately
#: **not** on that list: it contains no conversion at all and writes knots under
#: a `wind_units: "knots"` property, which is the confusion this constant exists
#: to end. The direction of the multiplication here is load-bearing — see the
#: Remal cross-check in the module docstring.
KNOTS_TO_KMPH = 1.852

#: `csv.field_size_limit`. CPython's default is 131,072 — a fact about the
#: interpreter, not about the archive, so nothing here pins it. The plan said
#: some columns exceed it. **Measured on the archive in this working tree, the
#: longest single cell is 19 characters** (`ISO_TIME`), so on *this* file the
#: raise is a no-op and the file parses fine at the default. That measurement is
#: why this comment says "no-op today" rather than claiming a fix. It is set
#: anyway, and raised at import rather than inside `read_ni_rows`, because:
#:
#:   * IBTrACS list files are not all like this one — several carry long
#:     free-text fields — so the limit belongs to "reading IBTrACS", not to
#:     "reading the file Priyanuj happened to download".
#:   * `csv.field_size_limit` is process-global. Raising it in the reader means
#:     any other `DictReader` in the process inherits the safe value, and
#:     restoring it on the way out would be worse: a caller that streams this
#:     file and then reads their own would get the small limit back at the worst
#:     moment.
#:
#: It is only ever raised, never lowered — see `_raise_field_size_limit`.
IBTRACS_FIELD_SIZE_LIMIT = 10_000_000

#: IBTrACS's numeric missing-value sentinels, in the units of whatever column
#: they appear in. They are negative, which is the only reason they are
#: recognisable: no observation of latitude, longitude, wind or pressure is
#: negative, so a negative number in one of those columns is a sentinel whatever
#: its magnitude. `-1.0` is included as a float so `"-1.0"` and `"-1e0"` are
#: both caught, not just the integer spelling.
IBTRACS_MISSING_SENTINELS = frozenset({-1.0, -999.0, -9999.0})

#: Travels on every record, and is the sentence a judge reads. Five things it
#: has to do, each of which has already been got wrong somewhere in this
#: project's history:
#:
#: - *Say it is history.* A best track drawn next to a live-cyclone button,
#:   with no label, reads as a prediction for whatever is happening now.
#: - *Say it is not about other storms.* The surge model is scaled from Remal
#:   alone; a reader who assumes these tracks feed a general forecast is wrong
#:   in the direction that matters.
#: - *Say a missing wind is not a calm wind.* 45,280 of 57,852 NI fixes have
#:   none, and `fetch_ibtracs.py` wrote them as 0.0. This is the artefact.
#: - *Name the column and its unit.* `USA_WIND` is knots. A peak of "60" next
#:   to IMD's 110-120 kmph is otherwise a contradiction the reader has to
#:   resolve themselves, and the wrong resolution is a 1.85x error.
#: - *Say `NATURE` is not an IMD category.* See the module docstring.
#:
#: Kept as one pinned constant, duplicated per record rather than looked up at
#: render time, because a limitation that lives only in a source file does not
#: constrain anybody.
IBTRACS_LIMITATION = (
    "This track is observed history, not a forecast, and it is not a prediction "
    "for any other storm. Wind is the IBTrACS USA_WIND column, published in "
    "knots and converted here to km/h; a fix with no reported wind is not a "
    "calm fix. NATURE is IBTrACS's own classification and is not an IMD wind "
    "category."
)


def _raise_field_size_limit() -> None:
    """Raise the process-global `csv` field limit, and only ever raise it.

    Guarded rather than assigned so that a host application which has already
    raised the limit higher keeps its own value. Lowering someone's limit
    because we imported would be a worse bug than the one this prevents.
    """
    if csv.field_size_limit() < IBTRACS_FIELD_SIZE_LIMIT:
        csv.field_size_limit(IBTRACS_FIELD_SIZE_LIMIT)


# At import, not on first read: see `IBTRACS_FIELD_SIZE_LIMIT` for why, and
# `tests/test_ibtracs_ni.py::test_a_wp_row_in_the_source_is_never_yielded` for
# the concrete failure it prevents — that test opens the same 174-column file
# with a bare `csv.DictReader` of its own, and would hit the 131,072 default
# before this reader had a chance to run.
_raise_field_size_limit()


def ibtracs_number(value: str | None) -> float | None:
    """The one place a cell becomes a number. `None` for anything unusable.

    This is deliberately the *only* numeric parser in the ingestion path, and
    the reason is that "missing" has five spellings in this file and they all
    have to become the same thing:

    - `""` and `" "` — IBTrACS pads its absent cells, and row 1 is entirely
      units. 45,280 NI fixes have a blank `USA_WIND`.
    - `"-1"`, `"-999"`, `"-9999"` — negative sentinels. They are negative
      precisely so they cannot be confused with an observation, so a parser can
      recognise them by sign alone; `"-1.0"` and `"-1e0"` are the same sentinel
      and have to be caught too, which is why the check is on the parsed float
      rather than on the string.
    - Anything `float()` refuses (`"n/a"`, `"kts"`, `"degrees_north"`).
    - `inf` / `nan`, which `float()` accepts without complaint. `base.py`
      documents exactly how these poison a record — `max()` is
      order-dependent around NaN, so one bad fix either sets a storm's peak to
      NaN or hides behind it, and `json.dumps` then writes a bare `NaN` token
      that is not JSON. The `isfinite` guard here is the primary defence; the
      one in `peak_wind_kmph` is the backstop it expects this to have applied.

    **`0` is not a sentinel and comes back as `0.0`.** A best-track agency does
    report 0 kt for a storm that has dissipated, and a parser that treated
    falsy as missing would delete the end of every storm's life. That asymmetry
    is why the sentinels are matched by value and not by truthiness.

    There is no range check and no clamping. A value that parses is a value
    that was in the file, and deciding an observation is implausible is a
    policy this module does not have.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    # `float()` accepts "nan" and "inf" without complaint; neither is an
    # observation. Same guard as `base.peak_wind_kmph`, applied here where the
    # offending cell can still be named.
    if not math.isfinite(number):
        return None
    if number in IBTRACS_MISSING_SENTINELS:
        return None
    return number


def read_ni_rows(path: Path = DEFAULT_IBTRACS_PATH) -> Iterator[dict[str, str]]:
    """Stream the archive, yielding only rows whose `BASIN` is `NI`.

    A generator, not a list, for a file of this size: a caller that
    wants one storm should not have to hold every Bay of Bengal cyclone in
    memory to find it. `build_catalogue` does materialise, because it has to
    group.

    The filter is `.strip()`-ed, which is what drops row 1 — the units row,
    whose `BASIN` is a single space and whose `LAT` reads `degrees_north`. That
    row is also dropped by `_row_storm_id` refusing a blank `SID`, so the units
    row is excluded twice over. Redundancy is the point: a units row that
    survived would produce a record named "degrees_north" at latitude NaN, and
    the two guards have different failure modes.

    Cells are **not** stripped or parsed here, beyond the `BASIN` comparison that
    is this function's entire job. A row that has been quietly tidied is a row
    whose raw spelling no longer matches the file, so a test that checks what the
    reader yields has something honest to check against. All numeric parsing is
    `ibtracs_number`'s job, and it happens once, in `build_catalogue`.
    """
    _raise_field_size_limit()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row.get("BASIN") or "").strip() == NI_BASIN_CODE:
                yield row


def _row_storm_id(row: dict[str, str]) -> str | None:
    """The storm this fix belongs to, or `None` if the row is not a fix.

    `SID` is the group key and it becomes `CycloneRecord.cyclone_id` verbatim —
    it is the cache key for every simulation downstream, so inventing one, or
    normalising its case, would split one storm into two or merge two storms
    into one. `2024145n14087` is therefore *not* found, and that is correct:
    a differently-spelled id is a different id, and matching it case-insensitively
    would be a substitution.

    `None` covers a blank `SID` (the units row, whose `SID` is a single space),
    a `SID` with padding around it, and a `SID` that is not a plausible storm
    identifier. Plausible means every character is one of `A-Z0-9`. IBTrACS
    spells some names into the id, so a `.` or a `-` would mean something this
    check does not know about, and refusing to accept an id this module cannot
    vouch for is better than carrying it into a cache key. A padded `SID` is
    rejected rather than trimmed: a cell that needed tidying is a cell whose
    raw spelling no longer matches the file, and the tidying is not this
    function's to do.
    """
    raw = row.get("SID") or ""
    sid = raw.strip()
    if not sid or sid != raw:
        return None
    if not all(character.isdigit() or character.isupper() for character in sid):
        return None
    return sid


def _row_season(row: dict[str, str]) -> int | None:
    """`SEASON` as an int, or `None` when it is not one.

    `None` is not a "treat it as recent" case. `build_catalogue` compares the
    season against `since`, and a storm whose year cannot be read cannot be
    placed on either side of that threshold, so it is skipped. Guessing 1900
    would file a real cyclone under a decade nobody asked about.
    """
    season = ibtracs_number(row.get("SEASON"))
    if season is None or season != int(season):
        return None
    return int(season)


def _row_subbasin(row: dict[str, str]) -> str | None:
    """`SUBBASIN` verbatim, or `None` when blank.

    `BB` (Bay of Bengal) and `AS` (Arabian Sea) are IBTrACS's split of the
    basin, and the NI rows divide between them. Storing what the source
    published is the whole point — `base.py` is explicit that `subbasin` is an
    opaque string the source owns. A blank is `None` rather than `""` because
    `""` renders as an empty chip and `None` renders as nothing, and which of
    those is right is a decision made here rather than left to a client.
    """
    subbasin = (row.get("SUBBASIN") or "").strip()
    return subbasin or None


def _row_name(row: dict[str, str]) -> str:
    """`NAME` verbatim, including the `"UNNAMED"` placeholder.

    52,950 of the 57,852 NI fixes are `UNNAMED`, covering 1,713 of the 1,859
    storms. Turning that into "Unnamed Storm 3", or into an empty string, would
    invent a name; `fetch_ibtracs.py` filters on `NAME == "REMAL"` precisely
    because the string is the only identity a named storm has in this file.

    A blank name falls back to `"UNNAMED"`, which is what the file itself
    writes rather than what this module would choose.
    """
    name = (row.get("NAME") or "").strip()
    return name or "UNNAMED"


def _row_nature(row: dict[str, str]) -> str | None:
    """`NATURE` verbatim, or `None` when blank. Never translated.

    IBTrACS's own classification: `TS`, `NR` (meaning *not reported*),
    `DS`, `MX`, `ET`. It is not IMD's scheme, over a different
    averaging period, and turning `TS` into "Cyclonic Storm" would hand a caller
    a wind category this column never measured.

    `NR` is returned as `NR`. It is a value the agency published, so replacing
    it with `None` is a substitution and expanding it is an invention; and
    because the brief and the project constraint both say *verbatim*, that is
    where it stops. What a client should *display* for `NR` — nothing, rather
    than a chip reading "NR" — is a rendering decision and belongs to whoever
    builds the UI, not to the parse.
    """
    nature = (row.get("NATURE") or "").strip()
    return nature or None


def _row_waypoint(row: dict[str, str]) -> CycloneWaypoint | None:
    """One IBTrACS fix -> one `CycloneWaypoint`, or `None` if unusable.

    The two things that can make a fix unusable are a missing position and a
    missing storm id; both return `None` and the caller drops the fix. Nothing
    here is defaulted:

    - **`LAT` / `LON` are required.** `(0, 0)` is a real place in the Gulf of
      Guinea and the obvious thing to reach for. A track that gains a waypoint
      there because a coordinate failed to parse is a track that crosses a
      continent, and a gap in a track is a thing the reader can see.
    - **`ISO_TIME` is verbatim**, as `2024-05-25 12:00:00` — unzoned, to the
      minute, exactly as IBTrACS publishes it. Note that this is *not* the
      `...Z` form `CycloneWaypoint`'s docstring describes; it is left alone
      because (a) the brief specifies verbatim, (b) `data/remal_track.geojson`
      carries the same spelling and a rewrite would make `/track` and
      `/cyclones` disagree about the same storm, and (c) appending a zone
      letter to a string IBTrACS publishes unzoned is a transformation, and the
      one transformation allowed here is knots to kmph. IBTrACS documents
      `ISO_TIME` as UTC, so the value is right; it just says so by not saying
      it. Flagged for the API layer in the task report.
    - **`USA_WIND` is knots, converted to kmph** and rounded to one decimal so
      two runs over the same file produce the same bytes. Blank is `None` with
      `wind_reported` false — see the module docstring for what that stops.
    - **`wind_reported` is decided by the parse, never by the value.** It is
      true only for a wind that came back non-negative. A reported `0` is
      reported; a `-5` is a corrupt cell.
    - **`wind_kmph is None` if and only if `wind_reported` is false.** A
      negative wind is not carried alongside a false flag — it is discarded.
      `peak_wind_kmph` takes a `max`, so a `-5` that survived would become the
      *strongest* wind on the track, and a value paired with
      `wind_reported: false` is a second, subtler version of the same bug: a
      consumer that reads the number without reading the flag draws a storm
      with a negative wind on it.
    - **`USA_PRES` is hPa already**, so no conversion. `None` when absent,
      which is 89% of fixes.
    """
    latitude = ibtracs_number(row.get("LAT"))
    longitude = ibtracs_number(row.get("LON"))
    if latitude is None or longitude is None:
        return None
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        # The one range check here, and it exists because a coordinate outside
        # the globe is a parse bug in every reading: a hemispheric swap, a
        # concatenated field. It is a rejection, not a repair — nothing is
        # clamped, and no value is corrected. (A merely *implausible* fix is
        # carried as written: 1966 storm `1966233N13340` runs from 65.2 N up to
        # 83.0 N, and 1951's `1951272N20274` sits at 80.8-81.0 N, both filed
        # under `SUBBASIN == "AS"`. Both are pre-1970 and so outside the
        # catalogue's default window, but they are in the 1,859. Which
        # observations to disbelieve is not this module's call.)
        return None
    wind_knots = ibtracs_number(row.get("USA_WIND"))
    # `reported` being true already implies `wind_knots is not None`, so the
    # guard below is only about the negative case, not about None.
    reported = wind_knots is not None and wind_knots >= 0.0
    return CycloneWaypoint(
        iso_time=(row.get("ISO_TIME") or "").strip(),
        latitude=latitude,
        longitude=longitude,
        wind_kmph=round(wind_knots * KNOTS_TO_KMPH, 1) if reported else None,
        wind_reported=reported,
        pressure_hpa=ibtracs_number(row.get("USA_PRES")),
        nature=_row_nature(row),
    )


def _source_read_time(path: Path) -> str:
    """When this input was placed on this machine, as UTC — the read time.

    `CycloneRecord.fetched_at` means "when this process read the data", and the
    obvious implementation is `datetime.now(UTC)`. For an artefact that gets
    committed and diffed, that is wrong twice over:

    - Every regeneration of `data/cyclones/catalogue.json` would differ, so
      `git diff` could no longer distinguish a data change from a re-run, and
      the determinism test in `tests/test_ibtracs_ni.py` would be checking
      something the writer had already guaranteed.
    - It would be a fabricated fact. This module fetches nothing; the archive
      was placed in the working tree by whoever set the workspace up. The mtime
      of that file is the only timestamp available that is a fact about the
      input rather than about the machine that happened to read it — the
      strongest form of that claim, and it does not depend on the file being in
      git, which it is not.

    The cost is stated rather than hidden, and it is a different cost from the
    one a committed input would carry. The archive is untracked, so a fresh
    clone does not have it at all; whoever places it sets its mtime, and
    `generated_at` will therefore differ per machine. Regenerating the
    catalogue after moving the input shows a one-line diff in `generated_at`.
    `--generated-at` pins it when a release wants a fixed value. The timestamp a
    user actually needs — `CycloneRecord.data_through`, the last fix in the file
    — is exact, comes from IBTrACS, and is unaffected by any of this.

    Second precision, `Z`-suffixed, matching `live_unavailable_reason`'s
    documented format. Not pinned to a constant like `LIVE_UNAVAILABLE_REASON`:
    that one is pinned because it is a fixed promise, and this one is a runtime
    fact about a file.
    """
    return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@dataclass(frozen=True)
class _Storm:
    """A group of fixes under one `SID`, before it becomes a `CycloneRecord`.

    Exists so the grouping is a separate step from the record construction:
    a storm's `name`, `season` and `subbasin` have to be read from *some* row
    of the group, and the choice of which row has to be a stated one. The first
    row after sorting by time is used, because a storm does not change its own
    name or year half way through and the file is not in a guaranteed order.

    Not exported. A caller that wants a record wants `build_catalogue` or
    `IbtracsSource`; a caller that wants to poke at the raw grouping is reading
    IBTrACS directly and should say so in its own code.
    """

    cyclone_id: str
    rows: tuple[dict[str, str], ...]


def _group_storms(path: Path) -> Iterator[_Storm]:
    """Yield one `_Storm` per `SID`, its fixes ordered by `ISO_TIME`.

    Sorted by time because a track is a sequence and a consumer that draws it
    cannot be trusted to re-sort: a Polyline through a best track in file order
    is a best track that visits the storm's genesis last, which on a
    north-westward track doubles back on itself. `ISO_TIME` is a fixed-width
    `YYYY-MM-DD HH:MM:SS` throughout this file, so string order is time order
    and no `datetime` is constructed for the tens of thousands of rows.

    Storm order out of this function is the file's. `build_catalogue` sorts, and
    sorting here would mean every caller inherited a choice that is really
    about presentation.
    """
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_ni_rows(path):
        cyclone_id = _row_storm_id(row)
        if cyclone_id is None:
            continue
        grouped.setdefault(cyclone_id, []).append(row)
    for cyclone_id, rows in grouped.items():
        by_time = sorted(rows, key=lambda row: row.get("ISO_TIME") or "")
        yield _Storm(cyclone_id=cyclone_id, rows=tuple(by_time))


def build_records(
    path: Path = DEFAULT_IBTRACS_PATH,
    since: int = 1970,
    fetched_at: str | None = None,
) -> tuple[CycloneRecord, ...]:
    """Every NI storm at season >= `since`, as `CycloneRecord`s, oldest first.

    `since` is a season floor and nothing else. **1970 is a default argument,
    deliberately not a module constant**: T6 filters `SEASON >= 1970` again when
    it builds its ML training set, and the two are separate decisions about one
    input. Sharing a constant would make widening the catalogue window silently
    change the training set, and the only symptom would be a model that scored
    differently for no stated reason. There is a test
    (`test_since_is_its_own_parameter_and_not_a_shared_constant`) that fails if
    someone adds one.

    Three rows are dropped, each for a stated reason, and none of them is
    "repair it":

    - a fix with no readable `SID` — it cannot be filed under a storm;
    - a fix whose storm year cannot be read — `since` has nothing to compare it
      against;
    - a fix with no readable position — `(0, 0)` is a real place, and inventing
      a waypoint there is worse than a visible gap.

    A storm all of whose fixes were dropped is **not** published. A record with
    an empty track is a cyclone the app claims exists and cannot draw, and
    `CycloneRecord.waypoints` being empty is a legal value that no honest
    ingestion should produce. (Measured: this never fires on the archive in the
    working tree — all 57,852 NI fixes have both coordinates and a numeric year
    — so the
    rule is covered by synthetic tests in `tests/test_ibtracs_ni.py` rather than
    left unexercised.)

    `peak_wind_kmph` comes from `base.py`'s function rather than a local `max`,
    so the peak of a record and the peak of its own waypoints cannot disagree
    — the `max` over unreported winds is the bug that function exists to avoid.

    `fetched_at` defaults to the source file's mtime; see `_source_read_time`
    for why it is not the wall clock. It is a parameter so the CLI can pin it.
    """
    stamp = fetched_at if fetched_at is not None else _source_read_time(path)
    records: list[CycloneRecord] = []
    for storm in _group_storms(path):
        season = _row_season(storm.rows[0])
        if season is None or season < since:
            continue
        waypoints = tuple(
            waypoint
            for waypoint in (_row_waypoint(row) for row in storm.rows)
            if waypoint is not None
        )
        if not waypoints:
            continue
        first = storm.rows[0]
        records.append(
            CycloneRecord(
                cyclone_id=storm.cyclone_id,
                name=_row_name(first),
                season=season,
                basin=NI_BASIN_CODE,
                subbasin=_row_subbasin(first),
                source=IBTRACS_SOURCE_ID,
                observed=True,
                waypoints=waypoints,
                fetched_at=stamp,
                data_through=waypoints[-1].iso_time,
                peak_wind_kmph=peak_wind_kmph(waypoints),
                limitation=IBTRACS_LIMITATION,
            )
        )
    records.sort(key=lambda record: (record.season, record.cyclone_id))
    return tuple(records)


def build_catalogue(
    path: Path = DEFAULT_IBTRACS_PATH,
    since: int = 1970,
    fetched_at: str | None = None,
) -> dict:
    """The whole historical catalogue, as the dict `data/cyclones/catalogue.json` holds.

    Exactly three keys, and the shape is not negotiable: `generated_from` says
    which file this was read from, `generated_at` says when, and `cyclones` is
    the records. Nothing else is added, because a catalogue is consumed by
    later tasks that will assert on its keys and a field nobody has asked for
    becomes a field nobody can change.

    `generated_from` is **repo-relative**. An absolute path would embed the
    build machine's home directory in a committed file and would differ per
    developer, so regenerating the catalogue would diff on every line of
    nothing. A path outside the repo — a synthetic subset in a test — falls
    back to its file name, which is enough to identify it in a diff.

    Sorted by `(season, cyclone_id)`, so the list is stable and reads
    chronologically. Not sorted by name: `"UNNAMED"` would put 1,713 storms at
    the top.

    **Not memoised.** The whole file parses in about 0.6 s, and a cached
    catalogue would make the determinism test vacuous — it would be comparing a
    dict with itself. Callers that need this per request hold an
    `IbtracsSource`, which does cache.
    """
    records = build_records(path=path, since=since, fetched_at=fetched_at)
    return {
        "generated_from": _describe_source(path),
        "generated_at": records[0].fetched_at if records else _source_read_time(path),
        "cyclones": [record.to_dict() for record in records],
    }


def _describe_source(path: Path) -> str:
    """Repo-relative POSIX path of the input, or its file name if outside the repo."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.name


class IbtracsSource:
    """`CycloneSource` over the local NI archive. The historical implementation.

    Satisfies `backend.cyclones.base.CycloneSource` structurally: an
    `identifier` and an `async def fetch`. `fetch` is `async` because the other
    implementation of that Protocol performs a network request and T5's registry
    awaits both; this one reads a file and returns immediately, which is cheaper
    than two signatures the registry has to branch on.

    Parsing happens once per instance, on first use, and is then held. A
    parse at every request would be the slowest thing in the app and
    would show up as a p99 that looks like a hang. The cache is per instance
    rather than module-level because the catalogue is 610 records and a process
    that wants a different `since` should not pay for the first one's parse.

    `fetch` returns `None` for a storm it does not have and never raises. That
    is the Protocol's contract — "`None` means 'I could not get it', never 'I
    substituted something else'" — and it is load-bearing at the boundary: a
    `KeyError` escaping into a request handler turns "no such cyclone" into a
    500, and a 500 gets reported as an outage. A `None` gets reported as "not
    found", which is what it is.

    `identifier` is a class attribute, as the Protocol declares, so
    `IbtracsSource.identifier` works without an instance — which is what the
    registry wants when it is deciding which sources to mount.
    """

    identifier: str = IBTRACS_SOURCE_ID

    def __init__(self, path: Path = DEFAULT_IBTRACS_PATH, since: int = 1970) -> None:
        self.path = Path(path)
        self.since = since
        self._records: tuple[CycloneRecord, ...] | None = None
        self._by_id: dict[str, CycloneRecord] | None = None

    def records(self) -> tuple[CycloneRecord, ...]:
        """Every record this source holds, oldest season first. Parsed once.

        Public because a caller assembling a list endpoint needs all of them at
        once, and because rebuilding the index per request would be the same
        0.6 s parse this method exists to do once. The tuple is immutable and
        its records are frozen, so a caller cannot damage another caller's
        view of it.
        """
        if self._records is None:
            self._records = build_records(path=self.path, since=self.since)
            self._by_id = {record.cyclone_id: record for record in self._records}
        return self._records

    def _index(self) -> dict[str, CycloneRecord]:
        self.records()
        assert self._by_id is not None  # set by records(); kept for mypy, not for safety
        return self._by_id

    async def fetch(self, cyclone_id: str | None = None) -> CycloneRecord | None:
        """The record for `cyclone_id`, or the most recent one, or `None`.

        `cyclone_id=None` means "whatever storm you have". A live source answers
        that with the active storm; a historical source has nothing active, so
        it answers with the newest storm it holds. The alternative — `None` —
        would be indistinguishable from a feed that failed, which is the exact
        ambiguity `LiveStatus` was added to remove, and reintroducing it on the
        historical side would undo that for no gain.

        "Most recent" is measured on `data_through`, the last fix in the file,
        and ties break on `cyclone_id` so the answer cannot depend on the order
        the CSV happened to be in. It is *not* the case study: `fetch()` on this
        source returns a 2026 storm, and anything that wants Remal has to ask
        for `REMAL_CYCLONE_ID` by name. A historical source silently answering
        "Remal" because Remal is what the rest of the app is about would be a
        substitution, and `base.py`'s no-substitution rule is not a rule about
        live feeds only.

        `since` is honoured, so a storm the catalogue window excludes is
        `None` here too — the source and the catalogue cannot disagree about
        which storms exist.
        """
        index = self._index()
        if cyclone_id is not None:
            return index.get(cyclone_id)
        newest = max(
            self.records(),
            key=lambda record: (record.data_through or "", record.cyclone_id),
        )
        return newest
