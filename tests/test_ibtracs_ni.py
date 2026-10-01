"""Tests for IBTrACS ingestion — the historical cyclone source, filtered to NI.

`ibtracs.NI.list.v04r01.csv` is a required local input in the working tree,
read offline. **It is not in git** — 27 MB, untracked, not ignored — so
`git status` reports it as `??` and every test here that needs it is a test
that cannot run without it. `csv.DictReader` yields 62,860 records (62,861 lines
counting the header) across 174 columns, and the file is *not* a clean
North-Indian-Ocean file despite the name: it carries 4,525 `BASIN == "WP"` rows
and 482 `BASIN == "NA"` rows, including a genuine Atlantic storm at longitude
-63.6. So the one property this file has to have is the filter, and most of the
tests below are different ways of asking the same question — can a non-NI row
reach a record?

Three ways this file can lie on a judge's screen, in order of how bad each looks:

1. **A WP or NA storm drawn as a North Indian Ocean cyclone.** 16.3 N, 119.1 E
   is the Philippines; 18.8 N, -63.6 is the Caribbean. Neither is the Sundarbans
   and neither has anything to say about Sagar Island.
2. **A missing wind reading drawn as a calm one.** 45,280 of the 57,852 NI
   fixes have a blank `USA_WIND`. Coerce those to 0.0 — which
   `backend/data_pipeline/fetch_ibtracs.py` still does when it writes
   `data/remal_track.geojson` — and the track stops dead mid-ocean, which for a
   real cyclone is a visible lie.
3. **A best track presented as a forecast.** These storms ended. The
   `limitation` string has to say so on the record itself, not in a changelog.

Two corrections to the plan this file was written from, both checked against
the committed CSV rather than assumed (see `test_two_basins_never_collide_into_one_id`):

- An IBTrACS `SID` is **13** characters, not 14, and its basin letter is at
  **index 7**, not 11: `<4-digit season><3-digit number><basin letter><5
  digits of lat/lon>`, e.g. `2024` `145` `N` `14087`.
- That letter is `'N'` on *every* row in the file — including all 5,007 WP and
  NA rows. So the SID cannot prove basin membership at any index. `BASIN` is
  the only authority, which is what makes `read_ni_rows`'s filter load-bearing
  rather than belt-and-braces.

The tests are grouped by which guarantee they defend, and the ones that cannot
fail against the real file (it is clean: no NI fix lacks a coordinate, no NI
`SEASON` fails to parse) get a synthetic subset instead. A test that cannot fail
is not a test.

Read with `tests/test_cyclones_base.py`, which owns the shape of a record.
This file owns what goes *into* one.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.cyclones.base import CycloneRecord, CycloneSource, peak_wind_kmph
from backend.cyclones.historical import (
    DEFAULT_IBTRACS_PATH,
    IBTRACS_LIMITATION,
    IBTRACS_SOURCE_ID,
    NI_BASIN_CODE,
    REMAL_CYCLONE_ID,
    IbtracsSource,
    build_catalogue,
    ibtracs_number,
    read_ni_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACK_GEOJSON = REPO_ROOT / "data" / "remal_track.geojson"


def _raw_rows() -> list[dict[str, str]]:
    """Every row of the committed CSV, unfiltered — the thing we must not trust."""
    with DEFAULT_IBTRACS_PATH.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _remal(cat: dict) -> dict:
    return next(c for c in cat["cyclones"] if c["cyclone_id"] == REMAL_CYCLONE_ID)


# ---------------------------------------------------------------------------
# 1. The parser. Every number in a record comes through this, and only this.
# ---------------------------------------------------------------------------


def test_missing_value_sentinels_are_not_numbers():
    # Measured on the real file: blanks and -1 both occur.
    for bad in (None, "", " ", "  ", "-1", "-999", "-9999", "-1.0", "n/a"):
        assert ibtracs_number(bad) is None, bad
    assert ibtracs_number("60") == 60.0
    assert ibtracs_number("0") == 0.0  # 0 is a real value, not a sentinel


def test_a_reported_zero_survives_the_parser():
    """The rule a best track depends on: 0 kt is an observation, blank is not.

    `peak_wind_kmph` in `base.py` filters on `wind_reported` and never on the
    value, so a reported 0 contributes to a storm's peak. That asymmetry only
    holds if the parser hands `0.0` through and a blank does not — and the one
    place it can go wrong is a parser that treats "falsy" as "missing".
    """
    assert ibtracs_number("0.0") == 0.0
    assert ibtracs_number("0.0") is not ibtracs_number(" ")


def test_a_non_finite_reading_never_becomes_a_number():
    """`inf` is the one value that would read as the strongest cyclone ever.

    `base.py` documents the failure in full: `max()` is order-dependent around
    NaN, and `json.dumps` writes a bare `NaN` token that is not JSON. The guard
    there is a backstop that explicitly expects the parser to have rejected it.
    """
    for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "1e400"):
        assert ibtracs_number(bad) is None, bad


# ---------------------------------------------------------------------------
# 2. The basin filter. The property the whole file is named after.
# ---------------------------------------------------------------------------


def test_only_north_indian_ocean_rows_are_read():
    # The placed file also holds WP (4,525) and NA (482) rows.
    rows = list(read_ni_rows())
    assert rows, "the file must yield rows"
    assert {r["BASIN"] for r in rows} == {"NI"}


def test_the_junk_row_is_dropped():
    # The file has exactly one row with BASIN == ' ' — the units row, where
    # LAT literally reads "degrees_north" and USA_WIND reads "kts".
    rows = list(read_ni_rows())
    assert all(r["BASIN"].strip() == "NI" for r in rows)
    junk = [r for r in _raw_rows() if r.get("BASIN") == " "]
    assert len(junk) == 1
    assert junk[0]["LAT"] == "degrees_north"


def test_every_ni_row_in_the_file_is_yielded_and_nothing_else():
    """The filter loses nothing: the yield is exactly the raw `BASIN == "NI"` rows.

    Counted rather than set-compared, so a row duplicated on the way out and a
    row dropped both show up.
    """
    raw = _raw_rows()
    assert sum(1 for r in raw if (r.get("BASIN") or "").strip() == NI_BASIN_CODE) == 57852
    assert len(list(read_ni_rows())) == 57852


def test_a_wp_row_in_the_source_is_never_yielded():
    """A direct check on the reader, independent of what the catalogue built.

    92 of the 1,859 SIDs in this file have fixes in more than one basin, and 57
    of those are at season >= 1970. They are real cross-basin cyclones: Vamei
    (2001) formed at 1.9 N, Bualoi (2025) ran from the Philippines into the Bay
    of Bengal, and IBTrACS files the Indian Ocean leg under `BASIN == "NI"` with
    `SUBBASIN == "BB"`. Keeping that leg is why this file contains those rows at
    all, and dropping the whole storm would delete 57 genuine Bay of Bengal
    tracks from the catalogue.

    **Which is why the leak has to be checked per row, by row identity, and why
    the first version of this test could not fail.** It compared yielded SIDs
    against the SIDs on WP rows — but all 87 WP SIDs also have NI rows, so a
    reader that yielded NI *and* WP rows left every one of those assertions
    satisfied. The review caught that by writing exactly that reader and watching
    the test pass.

    `(SID, ISO_TIME)` is used as the row identity instead, and the guard below
    is what makes it sound: the pair is unique across all 62,860 records in this
    file, so a WP row's key cannot coincide with an NI row's. That is a fact
    about the archive rather than an assumption, and it is asserted here rather
    than trusted, because if IBTrACS ever emitted two rows at one timestamp the
    test would need a different key and would say so.
    """
    with DEFAULT_IBTRACS_PATH.open(newline="") as fh:
        raw = list(csv.DictReader(fh))
    wp = [r for r in raw if r.get("BASIN") == "WP"]
    assert wp, "the source file must actually contain WP rows for this to test anything"

    def key(row: dict[str, str]) -> tuple[str, str]:
        return row["SID"], row["ISO_TIME"]

    assert len({key(r) for r in raw}) == len(raw), "(SID, ISO_TIME) is not a row key"

    ni = {r["SID"] for r in raw if (r.get("BASIN") or "").strip() == NI_BASIN_CODE}
    mixed = {r["SID"] for r in wp} & ni
    assert len(mixed) == 87, "measured: 87 SIDs have both WP and NI fixes"
    assert not ({r["SID"] for r in wp} - ni), "every SID here has some NI fix"

    yielded = list(read_ni_rows())
    # The two code-sensitive assertions. Either one alone fails for a reader
    # that leaks; the second also fails for a reader that leaks a WP row whose
    # storm is not in the catalogue window at all.
    assert not any(r["BASIN"] == "WP" for r in yielded)
    assert not ({key(r) for r in yielded} & {key(r) for r in wp})


def test_an_na_storm_in_the_caribbean_is_never_yielded():
    """The NA rows are the sharpest version of the WP case.

    `BASIN == "NA"` here is a real Atlantic storm at longitude -63.6, with
    `SUBBASIN == "CS"` (Caribbean Sea) — not an Indian Ocean subbasin at all.
    Drawing it inside a Sundarbans flood map would be indefensible, so the test
    names the storm rather than counting rows.
    """
    raw = _raw_rows()
    na = [r for r in raw if r.get("BASIN") == "NA"]
    assert na, "the source file must actually contain NA rows for this to test anything"
    assert any(float(r["LON"]) < 0 for r in na), "expected a western-hemisphere fix"
    assert {r["SUBBASIN"] for r in na} != {"BB", "AS"}

    yielded = list(read_ni_rows())
    # Its 12 NI fixes sit at 70 N / 40 E eighteen days later — a different
    # system entirely wearing the same id. They are kept because they are NI
    # rows; the 133 Caribbean fixes are all gone, and the negatives with them.
    assert "1932244N19296" in {r["SID"] for r in yielded}
    assert not any(float(r["LON"]) < 0 for r in yielded)
    assert not any(r["BASIN"] == "NA" for r in yielded)


def test_no_published_waypoint_lies_outside_the_north_indian_ocean():
    """The check a basin *label* cannot make, and the one that matters.

    `BASIN` says what IBTrACS filed a fix under; it does not say where the fix
    is. A record is only safe to draw on a Sundarbans map if every position in
    it is actually in the Indian Ocean, and that has to be measured from the
    coordinates rather than trusted from the label.

    Measured on the post-1970 NI rows this file yields: 0.7-31.6 N, 41.8-100.0
    E. The bounds below leave margin on all four sides. A Western Pacific fix
    needs longitude over 113 E to be in the Philippines and a North Atlantic
    one needs a negative longitude, so both fail this by a wide margin rather
    than by a rounding error.
    """
    for cyclone in build_catalogue()["cyclones"]:
        for waypoint in cyclone["waypoints"]:
            assert 0.0 <= waypoint["latitude"] <= 32.0, (cyclone["cyclone_id"], waypoint)
            assert 40.0 <= waypoint["longitude"] <= 101.0, (cyclone["cyclone_id"], waypoint)


def test_every_catalogue_id_came_from_a_row_that_says_ni():
    """The leak check that does not depend on parsing the SID grammar.

    Two levels, and the second is the one that can fail. The first — every
    catalogue id is the `SID` of some `BASIN == "NI"` row — would not have
    caught a leaked WP row, because all 87 WP SIDs in this file also have NI rows
    and so appear in that set regardless. The review found this by writing a
    reader that leaks and watching the subset assertion hold.

    So every *waypoint* is traced back to the specific row it came from, by
    `(SID, ISO_TIME)`, and required to be an NI row and not a WP or NA one. That
    is per-fix provenance rather than per-storm, and it is what actually
    establishes that nothing from another basin reached a record. The
    uniqueness of that key across the file is asserted rather than assumed.
    """
    raw = _raw_rows()

    def key(sid: str, iso_time: str) -> tuple[str, str]:
        return sid, iso_time

    assert len({key(r["SID"], r["ISO_TIME"]) for r in raw}) == len(raw)

    ni_rows = {
        key(r["SID"], r["ISO_TIME"])
        for r in raw
        if (r.get("BASIN") or "").strip() == NI_BASIN_CODE
    }
    foreign_rows = {
        key(r["SID"], r["ISO_TIME"])
        for r in raw
        if (r.get("BASIN") or "").strip() != NI_BASIN_CODE
    }
    assert foreign_rows, "the file must contain non-NI rows for this to test anything"
    assert " " in {r["SID"] for r in raw}, "sanity: the units row's SID is a space"
    assert " " not in {sid for sid, _ in ni_rows}, "sanity: the units row is not an NI row"

    cat = build_catalogue()
    ids = {c["cyclone_id"] for c in cat["cyclones"]}
    assert ids
    assert ids <= {sid for sid, _ in ni_rows}

    for cyclone in cat["cyclones"]:
        for waypoint in cyclone["waypoints"]:
            waypoint_key = key(cyclone["cyclone_id"], waypoint["iso_time"])
            assert waypoint_key in ni_rows, (cyclone["cyclone_id"], waypoint["iso_time"])
            assert waypoint_key not in foreign_rows


def test_two_basins_never_collide_into_one_id():
    # Review Focus #5. An IBTrACS SID is
    # "<4-digit season><3-digit number><basin letter><5 digits of lat/lon>",
    # so the letter is the 8th character (index 7), not the 12th.
    #
    # The plan this test came from said index 11. That is wrong on this file
    # twice over: `2024145N14087` is 13 characters with 'N' at index 7, and
    # index 11 is the second-to-last digit — so the planned assertion compared
    # every catalogue id against a digit, and failed.
    #
    # Kept as a grammar well-formedness check, not as the leak check — see
    # `test_every_catalogue_id_came_from_a_row_that_says_ni` and
    # `test_no_published_waypoint_lies_outside_the_north_indian_ocean` for that,
    # and `test_the_sid_letter_cannot_identify_the_basin` for why the letter is
    # not evidence of anything.
    cat = build_catalogue()
    ids = [c["cyclone_id"] for c in cat["cyclones"]]
    assert len(ids) == len(set(ids)), "duplicate cyclone_id in the catalogue"
    assert all(len(i) == 13 for i in ids), "SID is not 13 characters"
    assert {i[7] for i in ids} == {"N"}
    assert all(i[0:4].isdigit() and i[4:7].isdigit() and i[8:].isdigit() for i in ids)
    # The four-digit prefix is the season, so it must agree with the record's.
    assert {c["cyclone_id"][0:4] for c in cat["cyclones"]} == {
        str(c["season"]) for c in cat["cyclones"]
    }


def test_the_sid_letter_cannot_identify_the_basin():
    """Documents *why* the filter, not the SID, is what keeps WP and NA out.

    The `BASIN` column and the letter in the `SID` are different fields, and in
    this file the letter is `'N'` on every row of storm-id shape — including all
    4,525 WP rows and all 482 NA rows, one of which is a Caribbean hurricane. So
    a test (or a caller) that inferred the basin from the id would be wrong, and
    silently wrong, which is the kind that survives review.
    """
    raw = [r for r in _raw_rows() if len(r["SID"]) == 13]
    non_ni = [r for r in raw if (r.get("BASIN") or "").strip() != NI_BASIN_CODE]
    assert len(non_ni) == 5007
    assert {r["SID"][7] for r in non_ni} == {"N"}
    assert {r["SID"][7] for r in raw} == {"N"}


# ---------------------------------------------------------------------------
# 3. Remal — the one storm this app is a case study of.
# ---------------------------------------------------------------------------


def _committed_track_points() -> list[dict]:
    """The fixes in the committed `data/remal_track.geojson`, in file order.

    Built from **v04r00** by `backend/data_pipeline/fetch_ibtracs.py`; this
    ingestion reads **v04r01**. The two revisions do not hold the same number of
    fixes for Remal, which is the whole of the next two tests.
    """
    committed = json.loads(TRACK_GEOJSON.read_text())
    return [f for f in committed["features"] if f["geometry"]["type"] == "Point"]


def test_remal_is_present_in_the_catalogue():
    cat = build_catalogue()
    ids = [c["cyclone_id"] for c in cat["cyclones"]]
    assert REMAL_CYCLONE_ID in ids
    remal = _remal(cat)
    assert remal["name"] == "REMAL"
    assert remal["season"] == 2024
    assert remal["source"] == "ibtracs_v04r01_ni"
    assert remal["observed"] is True
    assert len(remal["waypoints"]) == 40
    assert remal["data_through"] == "2024-05-28 06:00:00"


def test_remal_contains_every_fix_of_the_committed_track():
    """`/track` and `/cyclones` must not tell two stories about the same storm.

    **The plan said the two files have to agree on the count, and they do not.**
    `data/remal_track.geojson` was built from IBTrACS **v04r00** and holds 19
    fixes covering the landfall window only (2024-05-25 12:00 to 2024-05-27
    18:00). This ingestion reads **v04r01**, which has 40 fixes covering the
    whole life of the storm (2024-05-23 12:00 to 2024-05-28 06:00). v04r01 is a
    later revision of the same archive with more agencies and more positions in
    it; the plan's expectation of 40 *GeoJSON* features looks like v04r01's
    count of fixes read onto the wrong file.

    The claim that actually matters is containment, not equality: all 19
    committed timestamps are present in the catalogue's 40, so the map endpoint
    is drawing a subset of the catalogue's track and the two cannot contradict
    each other. Asserting equality would have meant either rejecting the
    committed file or discarding 21 real fixes.
    """
    remal = _remal(build_catalogue())
    committed_times = [f["properties"]["iso_time"] for f in _committed_track_points()]
    catalogue_times = [w["iso_time"] for w in remal["waypoints"]]
    assert len(committed_times) == 19
    assert set(committed_times) <= set(catalogue_times)
    assert catalogue_times == sorted(catalogue_times)
    assert catalogue_times[0] < committed_times[0]
    assert catalogue_times[-1] > committed_times[-1]


def test_remal_positions_agree_with_the_committed_geojson_where_they_overlap():
    """Same fixes, same places, to within a revision's worth of drift.

    Half a degree is not a fudge factor. Measured across the 19 overlapping
    fixes, the largest v04r00/v04r01 position difference is 0.4 degrees — about
    44 km, roughly the distance a storm covers in three hours, which is what
    reanalysis of a fix between revisions looks like. A tolerance tight enough to
    catch a sign error (0.01) would fail on the archive's own revision drift, and
    a tolerance loose enough to pass a sign error (10) would accept a fix on the
    wrong side of the planet.
    """
    remal = _remal(build_catalogue())
    by_time = {w["iso_time"]: w for w in remal["waypoints"]}
    worst = 0.0
    for feature in _committed_track_points():
        waypoint = by_time[feature["properties"]["iso_time"]]
        lon, lat = feature["geometry"]["coordinates"]
        worst = max(worst, abs(waypoint["latitude"] - lat), abs(waypoint["longitude"] - lon))
        assert waypoint["latitude"] == pytest.approx(lat, abs=0.5)
        assert waypoint["longitude"] == pytest.approx(lon, abs=0.5)
    assert 0.0 < worst <= 0.5, f"expected real revision drift, saw {worst}"


def test_the_committed_geojson_shows_remal_going_calm_and_the_catalogue_does_not():
    """The bug this whole module exists to fix, visible in two committed files.

    `data/remal_track.geojson`, built from v04r00, carries `usa_wind_kt: 0.0` at
    five consecutive fixes between 2024-05-27 03:00 and 15:00. v04r01 reports
    48, 45, 40, 35 and 33 kt at those exact timestamps — Remal was a
    weakening tropical storm, not a dead one. The GeoJSON's zeros are v04r00
    blanks coerced to 0.0 by `fetch_ibtracs.py`, and a client drawing them
    shows a real cyclone stopping dead in the Bay of Bengal for twelve hours and
    then restarting.

    This is asserted here rather than left to `tests/test_track.py` because the
    two files sitting in the repository with contradictory winds for the same
    storm is the finding, and the reconciliation is that the catalogue is the
    one reading v04r01 and keeping unreported wind as `None`.
    """
    remal = _remal(build_catalogue())
    by_time = {w["iso_time"]: w for w in remal["waypoints"]}
    zeros = [
        f["properties"]["iso_time"]
        for f in _committed_track_points()
        if f["properties"]["usa_wind_kt"] == 0.0
    ]
    assert len(zeros) == 5, "the committed track's coerced blanks"
    for stamp in zeros:
        waypoint = by_time[stamp]
        assert waypoint["wind_reported"] is True
        assert waypoint["wind_kmph"] == pytest.approx(
            {  # v04r01's own USA_WIND for those five fixes, in knots
                "2024-05-27 03:00:00": 48,
                "2024-05-27 06:00:00": 45,
                "2024-05-27 09:00:00": 40,
                "2024-05-27 12:00:00": 35,
                "2024-05-27 15:00:00": 33,
            }[stamp]
            * 1.852,
            abs=0.05,
        )


def test_waypoints_keep_unreported_wind_as_unreported():
    cat = build_catalogue()
    remal = _remal(cat)
    unreported = [w for w in remal["waypoints"] if not w["wind_reported"]]
    assert unreported, "Remal has fixes with no reported wind"
    assert all(w["wind_kmph"] is None for w in unreported)


def test_remal_peak_wind_is_60_kt_and_agrees_with_imd():
    """The cross-check that catches a units bug immediately.

    IBTrACS carries `USA_WIND` in **knots**; IMD's documented landfall wind for
    Remal was 110-120 km/h, which is 59-65 kt. A peak of 60 kt is therefore a
    match, and a peak of 60 *km/h* — the unit bug `surge.py` documents in its
    `IMD_BANDS` history — would be a storm half the size of the one on record.
    """
    remal = _remal(build_catalogue())
    assert remal["peak_wind_kmph"] == pytest.approx(60 * 1.852, abs=0.05)
    assert 59.0 * 1.852 <= remal["peak_wind_kmph"] <= 65.0 * 1.852


def test_remal_nature_is_ibtracs_own_vocabulary():
    """`NATURE` is carried verbatim and is not translated to an IMD band.

    IMD's classification and IBTrACS's are different schemes over different
    averaging periods. Mapping one onto the other would let a caller read "TS"
    as an IMD "Cyclonic Storm", which is a claim about wind this column cannot
    support.
    """
    remal = _remal(build_catalogue())
    natures = {w["nature"] for w in remal["waypoints"]}
    assert natures == {"TS", "DS", "MX"}
    assert not natures & {"Cyclonic Storm", "Severe Cyclonic Storm", "D", "DD"}


def test_nature_nr_means_not_reported_and_is_still_carried_verbatim():
    """3,014 NI fixes read `NR` in `NATURE` — IBTrACS's "not reported".

    The plan says verbatim, and it is right: `NR` is a value the agency
    published, so dropping it would be a substitution and expanding it to
    "Tropical Storm" would be an invention. But it is not a classification, and
    a client that renders it as a chip is showing a missing value as a
    category. That is a rendering decision and it belongs to whoever builds the
    UI, so it is pinned here rather than fixed in the parser.
    """
    cat = build_catalogue()
    natures = {w["nature"] for c in cat["cyclones"] for w in c["waypoints"]}
    assert "NR" in natures
    assert natures <= {"TS", "NR", "DS", "MX", "ET", None}
    assert "TS" in natures, "the real classifications must still be present"


# ---------------------------------------------------------------------------
# 4. No fabricated data. Every value is the file's, or it is None.
# ---------------------------------------------------------------------------


def test_every_number_in_a_record_came_through_the_sentinel_parser():
    """`-9999` in a coordinate or a wind is not a coordinate and not a wind.

    A parser that did `float(cell)` and stopped would place a storm at latitude
    -9999 or report a 9,999 kt gust, and both survive every downstream range
    check because a float is a float. So: no value in the catalogue may equal a
    sentinel, at any nesting depth.
    """
    cat = build_catalogue()
    checked = 0
    for cyclone in cat["cyclones"]:
        for waypoint in cyclone["waypoints"]:
            for key in ("latitude", "longitude", "wind_kmph", "pressure_hpa"):
                value = waypoint[key]
                if value is None:
                    continue
                checked += 1
                assert value not in (-1.0, -999.0, -9999.0), (cyclone["cyclone_id"], key)
                assert value not in (-1, -999, -9999), (cyclone["cyclone_id"], key)
    assert checked > 0


def test_pressure_is_none_when_the_file_has_none():
    """Measured: `USA_PRES` is populated on 6,297 of 57,852 NI fixes (10.9%).

    The plan this task was written from said 48%. The rule it gave — "`None`
    when absent" — is what is implemented; the percentage was a description of
    the file, and the file says 10.9%. Filling the other 89% with an
    interpolated or default pressure would be the fabrication this project has
    been wrong about before.
    """
    with_pres = [
        w
        for c in build_catalogue()["cyclones"]
        for w in c["waypoints"]
        if w["pressure_hpa"] is not None
    ]
    assert with_pres, "IBTrACS does report some pressures"
    assert all(850.0 <= w["pressure_hpa"] <= 1080.0 for w in with_pres)
    # Every fix that has a pressure also has a wind: both come from the US
    # agency columns, and where one is missing the other is too. Measured: zero
    # rows in the whole file have `USA_PRES` without `USA_WIND`.
    assert all(w["wind_kmph"] is not None for w in with_pres)


def test_remals_pressure_dips_where_its_wind_peaks():
    """A cross-check that the parse is reading the right columns.

    Remal's 38 reported pressures fall monotonically from 1000 hPa on 23 May to
    a minimum of 977 hPa, and come back up. The minimum is at
    2024-05-26 14:00 — the same fix as the 60 kt peak wind. Two columns, two
    independent files, and the physics says they have to agree on when the storm
    was strongest, so a parser that swapped them or mis-scaled either would fail
    this without any reference to an expected value.

    The last two fixes report neither wind nor pressure and are the two the
    catalogue carries as `None` — the storm's dissipation is genuinely
    unobserved in this archive, and the record says so rather than showing 0.
    """
    waypoints = _remal(build_catalogue())["waypoints"]
    assert len(waypoints) == 40
    assert [w["pressure_hpa"] is None for w in waypoints].count(True) == 2
    assert waypoints[-1]["pressure_hpa"] is None
    assert waypoints[-1]["wind_kmph"] is None
    assert waypoints[-1]["wind_reported"] is False

    reported_pressure = [w for w in waypoints if w["pressure_hpa"] is not None]
    reported_wind = [w for w in waypoints if w["wind_kmph"] is not None]
    deepest = min(reported_pressure, key=lambda w: w["pressure_hpa"])
    strongest = max(reported_wind, key=lambda w: w["wind_kmph"])
    assert deepest["iso_time"] == "2024-05-26 14:00:00"
    assert deepest["pressure_hpa"] == 977.0
    assert deepest["iso_time"] == strongest["iso_time"]
    assert strongest["wind_kmph"] == pytest.approx(60 * 1.852, abs=0.05)
    assert waypoints[0]["pressure_hpa"] == 1000.0


def test_latitude_and_longitude_are_never_substituted():
    """A row missing `LAT` or `LON` is dropped, never defaulted to 0.

    (0, 0) is a real place in the Gulf of Guinea and an obvious default; a
    track that gains a waypoint there because a coordinate failed to parse is a
    track that crosses a continent.

    The bound checked here is deliberately the whole northern and eastern
    hemispheres rather than the Sundarbans bbox the app draws. Measured on this
    file, NI fixes run 0.7-83.0 N and 30.2-100.0 E: 147 of the 610 post-1970
    storms leave any 5-35 N / 55-95 E box, legitimately — Arabian Sea cyclones
    reach 46 E, Bay of Bengal cyclones cross into Myanmar at 100 E, and
    Vamei (2001) formed at 1.9 N. A tighter assertion would have looked
    thorough and failed on real cyclones.

    What a hemisphere check does catch is a sign flip, which is the realistic
    parse failure: a southern-hemisphere cyclone drawn in the northern one.
    """
    raw = [
        r
        for r in read_ni_rows()
        if not ((r["LAT"] or "").strip() and (r["LON"] or "").strip())
    ]
    assert not raw, "this file has no fix missing a coordinate; see the synthetic tests"

    cat = build_catalogue()
    for cyclone in cat["cyclones"]:
        assert cyclone["waypoints"], "a storm with no waypoints must not be published"
        for waypoint in cyclone["waypoints"]:
            assert 0.0 <= waypoint["latitude"] <= 90.0
            assert 0.0 <= waypoint["longitude"] <= 180.0


def test_the_unnamed_placeholder_is_preserved_as_the_file_wrote_it():
    """`UNNAMED` is IBTrACS's own label and 52,950 of the 57,852 fixes carry it.

    Prettifying it to "Unnamed Storm 3", or emptying it, invents a name. What
    is not allowed is a substitution, so the exact string is pinned.
    """
    names = {c["name"] for c in build_catalogue()["cyclones"]}
    assert "UNNAMED" in names
    assert "Remal" not in names, "the file is upper case and must stay that way"
    assert all(n == n.strip() and n for n in names)
    assert "REMAL" in names


# ---------------------------------------------------------------------------
# 5. The limitation string. A best track is not a forecast.
# ---------------------------------------------------------------------------


def test_every_record_carries_the_required_caveats():
    """`limitation` is mandatory and has no default, so this is the test that it
    says the things rather than some of them.

    Checked as substrings against the pinned constant, so editing the sentence
    to drop a caveat fails here rather than on a judge's screen.
    """
    required = (
        "observed history",
        "not a forecast",
        "not a prediction for any other storm",
        "no reported wind is not a calm fix",
        "USA_WIND",
        "knots",
        "NATURE is IBTrACS's own classification",
        "not an IMD wind category",
    )
    for needle in required:
        assert needle in IBTRACS_LIMITATION, f"limitation lost: {needle!r}"

    for cyclone in build_catalogue()["cyclones"]:
        assert cyclone["limitation"] == IBTRACS_LIMITATION
        assert cyclone["observed"] is True
        assert cyclone["source"] == IBTRACS_SOURCE_ID


# ---------------------------------------------------------------------------
# 6. Determinism — the property a committed artefact depends on.
# ---------------------------------------------------------------------------


def test_catalogue_is_deterministic():
    a = build_catalogue()
    b = build_catalogue()
    strip = lambda c: [  # noqa: E731
        {k: v for k, v in w.items() if k != "fetched_at"} for w in c["waypoints"]
    ]
    for x, y in zip(a["cyclones"], b["cyclones"]):
        assert (x["cyclone_id"], strip(x)) == (y["cyclone_id"], strip(y))


def test_the_catalogue_carries_no_timestamp_that_moves_between_runs():
    """`build_catalogue` is deliberately **not** memoised, so this is a real
    second parse of 62,860 records rather than the same dict handed back twice.
    """
    a = build_catalogue()
    b = build_catalogue()
    assert a["generated_at"] == b["generated_at"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generated_from_is_repo_relative():
    """An absolute path would embed the build machine's home directory in a
    committed file, and would differ per developer, so a regenerated catalogue
    would diff on every line of nothing."""
    cat = build_catalogue()
    assert cat["generated_from"] == "ibtracs.NI.list.v04r01.csv"
    assert not cat["generated_from"].startswith("/")
    assert "Users" not in cat["generated_from"]


def test_generated_at_is_derived_from_the_input_not_the_clock(tmp_path: Path):
    """`generated_at` is the input file's mtime, and this is the test that says so.

    The first version of this test asserted only the *shape* of the value —
    `endswith("Z")`, four leading digits, later than 2000, equal to the
    record's `fetched_at` — and all four hold under
    `datetime.now(UTC)`. The test that names the determinism constraint did not
    test it. A shape check is worth keeping and is not worth anything on its own.

    So the decisive assertion is against a file whose mtime this test sets
    itself. `os.utime` puts the input's timestamp at 2001-02-03T04:05:06Z, and
    a wall clock cannot produce that under any circumstances, so this fails for
    a `datetime.now()` implementation on any run, at any time of day, forever.
    Nothing here depends on when the suite happens to run.
    """
    subset = _write_csv(
        tmp_path / "subset.csv",
        ["2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00,TS,10.0,90.0,20,1000\n"],
    )
    os.utime(subset, (981173106, 981173106))  # 2001-02-03T04:05:06Z
    cat = build_catalogue(path=subset, since=0)
    assert cat["generated_at"] == "2001-02-03T04:05:06Z"
    assert cat["cyclones"][0]["fetched_at"] == "2001-02-03T04:05:06Z"
    # And it tracks the input: a day later on the same file, a day later out.
    os.utime(subset, (981173106 + 86_400, 981173106 + 86_400))
    assert build_catalogue(path=subset, since=0)["generated_at"] == "2001-02-04T04:05:06Z"


def test_generated_at_matches_the_placed_input_and_a_pin_overrides_it():
    """The same rule against the real file, plus the escape hatch.

    The expected value is computed from `DEFAULT_IBTRACS_PATH`'s mtime here
    rather than hard-coded, because the input is a local file whose mtime
    differs per machine — hard-coding it would make this fail on every clone
    that is not the one that generated the catalogue. What is pinned is the
    *relation*, which is the rule.

    `--generated-at` / the `fetched_at` argument then wins outright, which is
    what lets a release commit a catalogue whose timestamp does not move even
    when the input's mtime does.
    """
    expected = datetime.fromtimestamp(
        DEFAULT_IBTRACS_PATH.stat().st_mtime, tz=UTC
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    cat = build_catalogue()
    assert cat["generated_at"] == expected
    assert cat["generated_at"] == _remal(cat)["fetched_at"]
    assert all(c["fetched_at"] == expected for c in cat["cyclones"])

    pinned = "2026-10-01T00:00:00Z"
    pinned_cat = build_catalogue(fetched_at=pinned)
    assert pinned_cat["generated_at"] == pinned
    assert all(c["fetched_at"] == pinned for c in pinned_cat["cyclones"])
    # The pin is the only thing that changed: every other field is identical,
    # which is what makes it a pin and not a re-read.
    without = [{k: v for k, v in c.items() if k != "fetched_at"} for c in cat["cyclones"]]
    with_pin = [
        {k: v for k, v in c.items() if k != "fetched_at"} for c in pinned_cat["cyclones"]
    ]
    assert with_pin == without


def test_data_through_is_the_last_fix_not_the_read_time():
    """`base.py` is explicit that these are two different facts, and the reason
    is a client claiming a 2011 cyclone was updated a second ago. For a
    historical storm they are months apart, so the distinction is testable."""
    remal = _remal(build_catalogue())
    assert remal["data_through"] == remal["waypoints"][-1]["iso_time"]
    assert remal["data_through"] == "2024-05-28 06:00:00"
    assert remal["data_through"] != remal["fetched_at"]


# ---------------------------------------------------------------------------
# 7. `since` — a catalogue filter, and nothing else.
# ---------------------------------------------------------------------------


def test_since_defaults_to_1970_and_cuts_the_1859_ni_storms_to_610():
    cat = build_catalogue()
    seasons = [c["season"] for c in cat["cyclones"]]
    assert min(seasons) == 1970
    assert len(cat["cyclones"]) == 610
    assert len(build_catalogue(since=0)["cyclones"]) == 1859
    assert {c["season"] for c in build_catalogue(since=2024)["cyclones"]} == {
        2024,
        2025,
        2026,
    }


def test_since_is_its_own_parameter_and_not_a_shared_constant():
    """The catalogue's `since` and T6's `SEASON >= 1970` filter are two separate
    decisions about one input, and there is deliberately no module-level
    constant to share: the moment they share one, changing the catalogue window
    silently changes the ML training set, and the failure surfaces as a model
    that scored differently for no stated reason."""
    import backend.cyclones.historical as historical

    numeric_constants = [
        getattr(historical, name)
        for name in dir(historical)
        if name.isupper() and isinstance(getattr(historical, name), int)
    ]
    assert 1970 not in numeric_constants, "1970 must stay a default argument, not a constant"
    assert build_catalogue(since=1970)["cyclones"] == build_catalogue()["cyclones"]


def test_the_catalogue_is_season_sorted_so_the_list_is_stable():
    keys = [(c["season"], c["cyclone_id"]) for c in build_catalogue()["cyclones"]]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# 8. `IbtracsSource` — the Protocol implementation.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def source() -> IbtracsSource:
    return IbtracsSource()


def test_source_satisfies_the_protocol(source: IbtracsSource):
    assert isinstance(source, CycloneSource)
    assert source.identifier == IBTRACS_SOURCE_ID == "ibtracs_v04r01_ni"


def test_source_fetches_remal_by_id(source: IbtracsSource):
    record = asyncio.run(source.fetch(REMAL_CYCLONE_ID))
    assert isinstance(record, CycloneRecord)
    assert record.cyclone_id == REMAL_CYCLONE_ID
    assert record.name == "REMAL"
    assert record.season == 2024
    assert record.source == IBTRACS_SOURCE_ID
    assert record.observed is True
    assert record.basin == NI_BASIN_CODE
    assert record.subbasin == "BB"
    assert len(record.waypoints) == 40
    assert record.peak_wind_kmph == peak_wind_kmph(record.waypoints)
    assert record.data_through == record.waypoints[-1].iso_time
    assert record.limitation == IBTRACS_LIMITATION


def test_source_returns_none_for_an_unknown_id_rather_than_raising(source: IbtracsSource):
    """A missing storm and a broken feed are different states.

    `CycloneSource.fetch` is documented as "`None` means 'I could not get it',
    never 'I substituted something else'". An unknown id is where that matters
    most: a `KeyError` escaping into a request handler is how a not-found
    cyclone turns into a 500 and gets reported as an outage.

    The case-folded id is here on purpose. `2024145n14087` is not Remal; it is
    a different string, and matching it case-insensitively would be a
    substitution — two ids resolving to one record.
    """
    for missing in ("NOT_A_STORM", "2024145W14087", "", "2024145n14087", "2024145N1408"):
        assert asyncio.run(source.fetch(missing)) is None, missing


def test_source_without_an_id_returns_the_most_recent_storm(source: IbtracsSource):
    """`cyclone_id=None` means "whatever storm you have".

    A live source answers that with the active storm. A historical source has
    nothing active, so it returns the newest one it holds — the closest
    analogue, and the only reading under which `fetch()` returning `None` would
    be indistinguishable from a broken feed, which is what `LiveStatus` exists
    to prevent. "Most recent" is measured on `data_through`, not on file order,
    so it does not depend on how the CSV happens to be sorted.
    """
    record = asyncio.run(source.fetch())
    assert record is not None
    newest = max(r.data_through for r in source.records())
    assert record.data_through == newest
    # The newest NI storm in the placed file is 2026 season number 266, through
    # 2026-09-24 — not Remal, which is what a "most recent by file order"
    # implementation would have returned.
    assert record.cyclone_id == "2026266N18085"
    assert record.season == 2026


def test_source_ignores_an_id_the_basin_filter_rejected(source: IbtracsSource):
    """A WP storm's SID is well-formed, so only the record set can reject it."""
    # 1842298N11080 is a real NI storm, from before the catalogue's window.
    assert asyncio.run(source.fetch("1842298N11080")) is None
    # 1947223N16119 is a real WP storm sitting in the same file at 119.1 E.
    assert asyncio.run(source.fetch("1947223N16119")) is None


def test_source_records_are_the_catalogue(source: IbtracsSource):
    """The source and the catalogue cannot disagree.

    They are two views of one parse, so this is nearly free — but it is the
    assertion that stops a second, subtly different reader being added later.
    `backend/data_pipeline/fetch_ibtracs.py` already is one, for one storm, and
    it coerces a blank wind to 0.0.
    """
    assert [r.to_dict() for r in source.records()] == build_catalogue()["cyclones"]


# ---------------------------------------------------------------------------
# 9. Rules that cannot be tested against the real file.
# ---------------------------------------------------------------------------
#
# The committed CSV is clean: no NI fix is missing a coordinate and no NI
# `SEASON` fails to parse, so the two drop rules in `build_catalogue` never fire
# on it. They get a synthetic subset with the same header, which also pins the
# units row — the other thing a hand-made IBTrACS subset gets wrong.

MINIMAL_HEADER = "SID,SEASON,BASIN,SUBBASIN,NAME,ISO_TIME,NATURE,LAT,LON,USA_WIND,USA_PRES\n"


def _write_csv(path: Path, lines: list[str]) -> Path:
    path.write_text(MINIMAL_HEADER + "".join(lines), newline="")
    return path


def test_a_fix_missing_a_coordinate_is_dropped_not_defaulted(tmp_path: Path):
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00,TS,10.0,90.0,20,1000\n",
            # LAT blank: must not become 0.0, and must not become a waypoint.
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 06:00:00,TS,  ,90.0,20,1000\n",
            # LON is the -9999 sentinel: dropped, not drawn at -9999.
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 12:00:00,TS,10.5,-9999,20,1000\n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 18:00:00,TS,10.5,90.5,20,1000\n",
        ],
    )
    cat = build_catalogue(path=path, since=0)
    assert [c["cyclone_id"] for c in cat["cyclones"]] == ["2024001N10000"]
    waypoints = cat["cyclones"][0]["waypoints"]
    assert [w["iso_time"] for w in waypoints] == [
        "2024-01-01 00:00:00",
        "2024-01-01 18:00:00",
    ]


def test_the_units_row_is_not_a_storm(tmp_path: Path):
    """Row index 1 of a real IBTrACS file carries units, not observations."""
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            " ,Year, , , , , ,degrees_north,degrees_east,kts,hPa\n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00,TS,10.0,90.0,20,1000\n",
        ],
    )
    assert [c["cyclone_id"] for c in build_catalogue(path=path, since=0)["cyclones"]] == [
        "2024001N10000"
    ]


def test_a_storm_whose_season_cannot_be_read_is_skipped(tmp_path: Path):
    """`since` compares against `SEASON`, so an unreadable season cannot be
    placed on either side of the threshold. Guessing one would fabricate a year
    on a cyclone record."""
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            "2024001N10000,Year,NI,BB,TESTA,2024-01-01 00:00:00,TS,10.0,90.0,20,1000\n",
            "2024002N10000,2024,NI,BB,TESTB,2024-01-02 00:00:00,TS,11.0,90.0,20,1000\n",
        ],
    )
    assert [c["cyclone_id"] for c in build_catalogue(path=path, since=0)["cyclones"]] == [
        "2024002N10000"
    ]


def test_waypoints_are_time_ordered_regardless_of_file_order(tmp_path: Path):
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            "2024001N10000,2024,NI,BB,TESTA,2024-01-03 00:00:00,TS,10.0,90.0,20,1000\n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00,TS,10.0,90.0,20,1000\n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-02 00:00:00,TS,10.0,90.0,20,1000\n",
        ],
    )
    cyclone = build_catalogue(path=path, since=0)["cyclones"][0]
    assert [w["iso_time"] for w in cyclone["waypoints"]] == [
        "2024-01-01 00:00:00",
        "2024-01-02 00:00:00",
        "2024-01-03 00:00:00",
    ]
    assert cyclone["data_through"] == "2024-01-03 00:00:00"


def test_blank_wind_is_none_and_reported_zero_is_zero(tmp_path: Path):
    """The distinction, on a file small enough to read all of it."""
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00,TS,10.0,90.0, , \n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 06:00:00,TS,10.0,90.0,0,1000\n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 12:00:00,TS,10.0,90.0,20,1000\n",
        ],
    )
    cyclone = build_catalogue(path=path, since=0)["cyclones"][0]
    blank, zero, twenty = cyclone["waypoints"]
    assert (blank["wind_kmph"], blank["wind_reported"]) == (None, False)
    assert (blank["pressure_hpa"], blank["nature"]) == (None, "TS")
    assert (zero["wind_kmph"], zero["wind_reported"]) == (0.0, True)
    assert twenty["wind_kmph"] == pytest.approx(37.0, abs=0.05)  # 20 kt
    assert twenty["pressure_hpa"] == 1000.0
    # 0 is a real observation and takes part in the max; the blank does not.
    assert cyclone["peak_wind_kmph"] == pytest.approx(37.04, abs=0.05)


def test_a_blank_nature_is_none_not_an_empty_string(tmp_path: Path):
    """`""` and `None` are different things to a client: one renders as an empty
    chip, the other as nothing at all. Which one a blank becomes is a decision,
    so it is made here and tested."""
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00, ,10.0,90.0,20,1000\n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 06:00:00,NR,10.0,90.0,20,1000\n",
        ],
    )
    blank, nr = build_catalogue(path=path, since=0)["cyclones"][0]["waypoints"]
    assert blank["nature"] is None
    assert nr["nature"] == "NR"


def test_a_storm_with_no_usable_fix_is_not_published(tmp_path: Path):
    """Every fix dropped means no track, and a trackless record claims a cyclone
    whose path nobody can draw. It is skipped, and the storm count reflects that
    rather than the row count."""
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00,TS, , ,20,1000\n",
            "2024002N10000,2024,NI,BB,TESTB,2024-01-02 00:00:00,TS,11.0,90.0,20,1000\n",
        ],
    )
    assert [c["cyclone_id"] for c in build_catalogue(path=path, since=0)["cyclones"]] == [
        "2024002N10000"
    ]


def test_a_negative_wind_is_treated_as_unreported(tmp_path: Path):
    """A negative wind is not a storm blowing the other way.

    The brief's rule is "`wind_reported` is `True` only when `USA_WIND` parsed
    to a non-negative float", and it matters: without it a -1 that escaped the
    sentinel table would become the *strongest* wind on a track, because
    `peak_wind_kmph` takes a max.
    """
    path = _write_csv(
        tmp_path / "subset.csv",
        [
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 00:00:00,TS,10.0,90.0,-5,1000\n",
            "2024001N10000,2024,NI,BB,TESTA,2024-01-01 06:00:00,TS,10.0,90.0,20,1000\n",
        ],
    )
    cyclone = build_catalogue(path=path, since=0)["cyclones"][0]
    assert (cyclone["waypoints"][0]["wind_reported"], cyclone["waypoints"][0]["wind_kmph"]) == (
        False,
        None,
    )
    assert cyclone["peak_wind_kmph"] == pytest.approx(37.04, abs=0.05)


def test_an_unnamed_storm_keeps_the_files_placeholder(tmp_path: Path):
    path = _write_csv(
        tmp_path / "subset.csv",
        ["2024001N10000,2024,NI,BB,UNNAMED,2024-01-01 00:00:00,TS,10.0,90.0,20,1000\n"],
    )
    assert build_catalogue(path=path, since=0)["cyclones"][0]["name"] == "UNNAMED"


# ---------------------------------------------------------------------------
# 10. The CLI. Deterministic bytes, no network.
# ---------------------------------------------------------------------------


def _run_cli(out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.data_pipeline.ingest_ibtracs_ni",
            "--out",
            str(out),
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_cli_writes_byte_identical_output_twice(tmp_path: Path):
    """The check the in-process determinism test cannot make.

    `build_catalogue` guarantees a stable dict. What it cannot guarantee is
    that the *serialisation* is stable — key order, float formatting, and any
    timestamp the writer adds are all downstream of it. So the file is written
    twice and compared byte for byte, which is the property a committed
    artefact actually needs.
    """
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for out in (first, second):
        result = _run_cli(out)
        assert result.returncode == 0, result.stderr
    assert first.read_bytes() == second.read_bytes()

    payload = json.loads(first.read_text())
    assert set(payload) == {"generated_from", "generated_at", "cyclones"}
    assert REMAL_CYCLONE_ID in {c["cyclone_id"] for c in payload["cyclones"]}


def test_cli_prints_the_storm_count_and_the_output_path(tmp_path: Path):
    out = tmp_path / "catalogue.json"
    result = _run_cli(out)
    assert result.returncode == 0, result.stderr
    assert "610" in result.stdout, result.stdout
    assert str(out) in result.stdout
    assert out.exists()


def test_cli_honours_a_pinned_generated_at(tmp_path: Path):
    """The escape hatch for the mtime caveat in `generated_at`."""
    out = tmp_path / "catalogue.json"
    result = _run_cli(out, "--generated-at", "2026-10-01T00:00:00Z")
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text())["generated_at"] == "2026-10-01T00:00:00Z"


def test_committed_catalogue_matches_the_code():
    """`data/cyclones/catalogue.json` is a build product and it is committed.

    Nothing stops it going stale except this: the storm list and Remal's
    40-waypoint track are re-derived from the CSV and compared. A full
    byte-comparison is deliberately not asserted, because the file is written
    on another machine whose copy of the input has a different mtime.
    """
    committed_path = REPO_ROOT / "data" / "cyclones" / "catalogue.json"
    assert committed_path.exists(), "run: python -m backend.data_pipeline.ingest_ibtracs_ni"
    committed = json.loads(committed_path.read_text())
    assert set(committed) == {"generated_from", "generated_at", "cyclones"}
    assert committed["generated_from"] == "ibtracs.NI.list.v04r01.csv"

    fresh = build_catalogue()
    fresh_ids = [c["cyclone_id"] for c in fresh["cyclones"]]
    assert [c["cyclone_id"] for c in committed["cyclones"]] == fresh_ids
    assert REMAL_CYCLONE_ID in fresh_ids

    index = fresh_ids.index(REMAL_CYCLONE_ID)
    # Round-tripped through JSON so tuples from the dataclass compare equal to
    # the lists a parsed file holds.
    assert json.loads(json.dumps(fresh["cyclones"][index])) == committed["cyclones"][index]
