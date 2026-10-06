"""The ATCF parser, against real bytes rather than from memory.

The fixture is six lines copied verbatim from
`https://ftp.nhc.noaa.gov/atcf/archive/2025/aal012025.dat.gz`, fetched
2026-10-01. Atlantic, because the North Indian Ocean has no reachable public
ATCF source — see `backend/cyclones/live.py`. Parsing Atlantic bytes proves the
format; the basin filter proves the filtering.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from backend.cyclones.atcf import (
    KNOTS_TO_KMPH,
    atcf_storm_id,
    parse_atcf,
    parse_atcf_line,
    parse_hemispheric_coordinate,
)

FIXTURE = Path(__file__).parent / "fixtures" / "aal012025_sample.dat"


# --- the two traps ----------------------------------------------------------


def test_the_leading_column_is_a_lead_time_not_a_latitude() -> None:
    """Field 5 is hours. Reading it as latitude puts the storm off the planet.

    This is the single most damaging mistake available in this format, because
    the result is a number in latitude range if you only check "is it a number"
    and impossible if you check range — and it shifts every subsequent field.
    """
    waypoint = parse_atcf_line(
        "AL, 01, 2025062218, 01, CARQ, -24, 302N,  573W,  20,    0, LO,  34, AAA,"
    )
    assert waypoint is not None
    assert waypoint.latitude == pytest.approx(30.2)
    assert waypoint.longitude == pytest.approx(-57.3)
    assert waypoint.iso_time == "2025-06-22 18:00:00"
    assert waypoint.wind_kmph == pytest.approx(20 * KNOTS_TO_KMPH, abs=0.05)
    assert waypoint.nature == "LO"

    # And the whole fixture agrees, which a single line cannot show.
    for waypoint in parse_atcf(FIXTURE.read_text(), basin="AL"):
        assert -90 <= waypoint.latitude <= 90
        assert -180 <= waypoint.longitude <= 180


def test_longitude_sign_comes_from_the_hemisphere_not_a_sign_character() -> None:
    """`573W` is -57.3. There is no minus anywhere in the field.

    Getting this wrong places an Atlantic storm in the Indian Ocean, which is
    roughly 5,000 km east — large enough to be obvious to a reader and small
    enough to survive a demo.
    """
    assert parse_hemispheric_coordinate("302N") == pytest.approx(30.2)
    assert parse_hemispheric_coordinate("302S") == pytest.approx(-30.2)
    assert parse_hemispheric_coordinate("573W") == pytest.approx(-57.3)
    assert parse_hemispheric_coordinate("573E") == pytest.approx(57.3)
    assert parse_hemispheric_coordinate("  302N ") == pytest.approx(30.2)
    assert parse_hemispheric_coordinate("0N") == pytest.approx(0.0)
    assert parse_hemispheric_coordinate("0W") == pytest.approx(0.0)


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "N", "abc", "30.2N", "-302N", "302X", "3O2N"],
)
def test_malformed_coordinates_are_none_not_an_exception(value) -> None:
    """One bad field must cost one fix, not the whole operational feed."""
    assert parse_hemispheric_coordinate(value) is None


# --- the fixture ------------------------------------------------------------


def test_the_fixture_is_six_real_lines() -> None:
    """Guard the fixture itself, so a truncated download cannot go unnoticed."""
    lines = [ln for ln in FIXTURE.read_text().splitlines() if ln.strip()]
    assert len(lines) == 6
    for line in lines:
        assert line.startswith("AL, 01, ")


def test_the_fixture_parses_to_six_waypoints() -> None:
    waypoints = parse_atcf(FIXTURE.read_text(), basin="AL")
    assert len(waypoints) == 6
    assert all(isinstance(w.latitude, float) for w in waypoints)
    assert all(math.isfinite(w.longitude) for w in waypoints)


def test_the_basin_filter_drops_what_it_is_asked_to_drop() -> None:
    """The fixture is Atlantic. Asking for the North Indian Ocean must be empty.

    This is the assertion that keeps a live feed from showing an Atlantic storm
    in a delta map, which would be the most spectacular possible wrong answer
    from this feature.
    """
    text = FIXTURE.read_text()
    assert parse_atcf(text, basin="AL")
    assert parse_atcf(text, basin="IO") == ()
    assert parse_atcf(text, basin="IO") == parse_atcf(text, basin="io")


def test_a_real_io_line_parses() -> None:
    """One synthetic IO fix, because no real one is published to fetch."""
    waypoint = parse_atcf_line(
        "IO, 01, 2025062218, 01, TEST, 0, 152N, 845E, 45, 990, TS,"
    )
    assert waypoint is not None
    assert waypoint.latitude == pytest.approx(15.2)
    assert waypoint.longitude == pytest.approx(84.5)
    assert waypoint.pressure_hpa == 990.0
    assert waypoint.nature == "TS"
    assert waypoint.wind_kmph == pytest.approx(45 * KNOTS_TO_KMPH, abs=0.05)


# --- unreported wind --------------------------------------------------------


def test_a_blank_wind_is_unreported_not_calm() -> None:
    """The rule the whole project has been bitten by (MEMORY.md §31)."""
    waypoint = parse_atcf_line("IO, 01, 2025062218, 01, X, 0, 152N, 845E,   ,   , TS,")
    assert waypoint is not None
    assert waypoint.wind_kmph is None
    assert waypoint.wind_reported is False


def test_a_zero_pressure_is_unavailable_not_zero_pascals() -> None:
    """ATCF writes 0 for "no pressure reading", and 0 hPa is not a reading."""
    waypoint = parse_atcf_line("IO, 01, 2025062218, 01, X, 0, 152N, 845E, 40, 0, TS,")
    assert waypoint is not None
    assert waypoint.pressure_hpa is None
    assert waypoint.wind_kmph == pytest.approx(40 * KNOTS_TO_KMPH, abs=0.05)


# --- malformed input --------------------------------------------------------


def test_malformed_lines_are_skipped_not_fatal() -> None:
    """A hand-edited operational feed must not read as "no storms".

    This is the one situation where reporting a storm actually matters, so a
    parser that raises on the first bad line is worse than one that drops it.
    """
    text = "\n".join(
        [
            "",
            "   ",
            "; a comment header",
            "garbage",
            "IO, 01, 2025062218, 01, XYZ, 0, 302N, 573W, 20, 0, TS,",
            "IO, 01, notadate, 01, XYZ, 0, 302N, 573W, 20, 0, TS,",
            "IO, 01, 2025062218, 01, XYZ, 0, badlat, 573W, 20, 0, TS,",
            "ZZ, 01, 2025062218, 01, XYZ, 0, 302N, 573W, 20, 0, TS,",
            "IO, 01, 2025062218, 01, XYZ, 0, 302N, 573W, 20, 0, TS,",
        ]
    )
    waypoints = parse_atcf(text, basin="IO")
    assert len(waypoints) == 2
    assert all(w.latitude == pytest.approx(30.2) for w in waypoints)


def test_a_short_line_is_rejected() -> None:
    assert parse_atcf_line("IO, 01, 2025062218") is None


def test_a_valid_timestamp_is_accepted() -> None:
    """The negative test below must not be satisfied by rejecting everything."""
    waypoint = parse_atcf_line("IO, 01, 2025062218, 01, X, 0, 152N, 845E, 45, 990, TS,")
    assert waypoint is not None
    assert waypoint.iso_time == "2025-06-22 18:00:00"


def test_an_unparseable_timestamp_is_rejected() -> None:
    # 2025023000 is 30 February: rejected because the calendar is built, not
    # range-checked. Range-checking day <= 31 accepts it, and the track then
    # draws a storm three days before it existed.
    for stamp in ("2025023000", "2025062225", "20250622", "2025-06-2218", "2024130100"):
        assert parse_atcf_line(f"IO, 01, {stamp}, 01, X, 0, 152N, 845E, 45, 990, TS,") is None


# --- ordering and identity --------------------------------------------------


def test_waypoints_come_back_sorted_by_time() -> None:
    """ATCF fixes are not ordered; a track drawn in feed order is a scribble."""
    text = "\n".join(
        [
            "IO, 01, 2025062406, 01, X, 0, 152N, 845E, 45, 990, TS,",
            "IO, 01, 2025062218, 01, X, 0, 152N, 845E, 30, 995, TS,",
            "IO, 01, 2025062300, 01, X, 0, 152N, 845E, 40, 992, TS,",
        ]
    )
    times = [w.iso_time for w in parse_atcf(text, basin="IO")]
    assert times == sorted(times)
    assert times[0] == "2025-06-22 18:00:00"


def test_the_storm_id_is_built_from_the_basin_and_number() -> None:
    assert atcf_storm_id("IO, 01, 2025062218".split(",")) == "IO01"
    assert atcf_storm_id("AL, 01, 2025062218".split(",")) == "AL01"
    assert atcf_storm_id("ZZ, 01, 2025062218".split(",")) is None
    assert atcf_storm_id(["IO"]) is None

def test_the_waypoint_carries_the_storm_number() -> None:
    waypoint = parse_atcf_line("IO, 05, 2025062218, 01, X, 0, 152N, 845E, 40, 0, TS,")
    assert waypoint is not None
    assert waypoint.storm_number == "05"


def test_ibtracs_waypoints_do_not_carry_a_storm_number() -> None:
    """`/track` payloads must not grow a NULLed storm-number key."""
    from backend.cyclones.registry import registry

    waypoint = registry().get("2024145N14087").waypoints[0]
    assert "storm_number" not in waypoint.to_dict()
