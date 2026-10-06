"""ATCF b-deck parsing. The live cyclone source's wire format.

## Why this module exists

`react-native-maps` is gone from the app (a Google Maps API key, which Expo Go
cannot carry, is why the Android map rendered black). The replacement draws the
map, but it also lets this project offer a *live* cyclone rather than only
historical ones.

**No public North Indian Ocean ATCF source was reachable when this was written.**
Every candidate was probed and the results are in the plan's ADR; NHC's own
archives carry b-decks for the Atlantic, Central and Eastern Pacific basins and
not one `io*` file in any year from 2010 to 2025. So the live provider reports
`live_unavailable` honestly by default. This module exists so that the day a
source opens, the code that reads it is already correct and tested against real
bytes rather than written from memory on the day.

## The wire format, and the trap in it

ATCF is comma-delimited, not the fixed-column layout its reputation suggests.
Verified against a real file fetched from
`https://ftp.nhc.noaa.gov/atcf/archive/2025/aal012025.dat.gz` on 2026-10-01:

```
AL, 01, 2025062218, 01, CARQ, -24, 302N,  573W,  20,    0, LO,  34, AAA, ...
 ^0  ^1  ^2         ^3  ^4   ^5   ^6    ^7    ^8    ^9  ^10 ^11
```

**Field 5 is a lead time in hours, not a latitude.** It ranges from -24 to 204
on a whole-hour grid. A parser that reads it as latitude puts the storm at
302 degrees south on its first fix, which is not a position on Earth and is the
kind of error a coordinate test would not catch if it only checked plausibility.
`test_the_leading_column_is_a_lead_time_not_a_latitude` pins it.

**Longitude carries no sign character.** The hemisphere letter is the whole of
the sign, so `573W` is -57.3 and a parser that assumes a leading minus will
place the storm in the Indian Ocean. `parse_hemispheric_coordinate` is the only
place that decision is made, and it is tested in both hemispheres in both
positions.

## Units

ATCF sustained wind is in **knots**. The record is km/h, so the conversion is
× 1.852. That is a different averaging period from the IMD 3-minute means the
rest of the app uses, which `AtcfLiveSource` discloses rather than hides.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable

from backend.cyclones.base import CycloneWaypoint

#: ATCF wind is a 1-minute mean sustained wind in knots.
KNOTS_TO_KMPH = 1.852

#: Basin codes this module will accept. IO is the North Indian Ocean.
BASIN_CODES = ("AL", "WP", "EP", "CP", "IO", "NI", "SH")


def parse_hemispheric_coordinate(value: str | None) -> float | None:
    """ATCF's ``302N`` / ``573W`` into 30.2 / -57.3.

    The format is digits followed by a hemisphere letter, with the decimal point
    implied before the final digit. The **letter is the only sign**: there is no
    leading minus anywhere in the field, so a parser that assumes one reads every
    western longitude as positive.

    Returns ``None`` rather than raising, because a single malformed field in a
    12,000-line operational feed should cost one fix, not the whole feed.
    """
    if value is None:
        return None
    text = value.strip().upper()
    if len(text) < 2 or text[-1] not in "NSEW":
        return None
    digits = text[:-1]
    if not digits.isdigit():
        return None
    magnitude = int(digits) / 10.0
    return -magnitude if text[-1] in ("S", "W") else magnitude


def _wind_to_kmph(value: str | None) -> float | None:
    """Field 8 from knots to km/h, or ``None`` when the feed left it blank.

    A blank wind is *unreported*, not calm. This is the same rule the IBTrACS
    loader follows and for the same reason: the app has been bitten by a blank
    rendering as a zero (MEMORY.md §31), and the caller is expected to set
    ``wind_reported=False`` rather than show 0 km/h.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        knots = float(text)
    except ValueError:
        return None
    if not math.isfinite(knots) or knots < 0:
        return None
    return round(knots * KNOTS_TO_KMPH, 1)


def _pressure_to_hpa(value: str | None) -> float | None:
    """Field 9. ATCF pressure is already in hPa.

    ``0`` means "not available" in ATCF, so it becomes ``None`` rather than a
    reading of zero pascals, which would be a physically absurd number to carry
    into an advisory.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        pressure = float(text)
    except ValueError:
        return None
    if not math.isfinite(pressure) or pressure <= 0:
        return None
    return pressure


def _timestamp_to_iso(value: str | None) -> str | None:
    """ATCF's ``YYYYMMDDHH`` into an ISO 8601 UTC instant.

    The feed is UTC and carries no minutes, so the instant is on the hour. The
    spelling matches IBTrACS's own ``ISO_TIME`` (``YYYY-MM-DD HH:MM:SS``, no
    ``Z``) rather than inventing a third format: `/track` and `/cyclones` would
    otherwise disagree about how a timestamp is written, for no gain.

    The calendar is built rather than range-checked. Checking ``1 <= day <= 31``
    alone accepts the 30th of February, which sorts to a plausible-looking time
    on a track that then draws a storm three days before it existed.
    """
    if value is None:
        return None
    text = value.strip()
    if len(text) != 10 or not text.isdigit():
        return None
    year, month, day, hour = int(text[0:4]), int(text[4:6]), int(text[6:8]), int(text[8:10])
    if not 1900 <= year <= 2100:
        return None
    try:
        stamp = datetime(year, month, day, hour)
    except ValueError:
        return None
    return f"{stamp.year:04d}-{stamp.month:02d}-{stamp.day:02d} {stamp.hour:02d}:00:00"


def parse_atcf_line(line: str) -> CycloneWaypoint | None:
    """One comma-delimited ATCF fix, or ``None`` if it cannot be read.

    Field 5 (the lead-time offset) is read only to be discarded. Keeping the
    index explicit in the unpacking is deliberate: it documents that the field
    is consumed and ignored, rather than silently shifting every later field.
    """
    if not line or not line.strip():
        return None
    fields = [part.strip() for part in line.split(",")]
    if len(fields) < 11:
        return None

    basin = fields[0].strip().upper()
    if basin not in BASIN_CODES:
        return None

    iso_time = _timestamp_to_iso(fields[2])
    latitude = parse_hemispheric_coordinate(fields[6])
    longitude = parse_hemispheric_coordinate(fields[7])
    if iso_time is None or latitude is None or longitude is None:
        return None

    wind_kmph = _wind_to_kmph(fields[8])
    nature = fields[10].strip() or None
    # Field 1 is ATCF's own storm number. The record id is built from it —
    # discarding it is what once made the live record carry a fabricated id.
    storm_number = fields[1].strip() or None

    return CycloneWaypoint(
        iso_time=iso_time,
        latitude=latitude,
        longitude=longitude,
        wind_kmph=wind_kmph,
        wind_reported=wind_kmph is not None,
        pressure_hpa=_pressure_to_hpa(fields[9]),
        nature=nature,
        storm_number=storm_number,
    )


def parse_atcf(text: str, basin: str = "IO") -> tuple[CycloneWaypoint, ...]:
    """Every readable fix for `basin`, sorted by time.

    Unreadable lines are skipped rather than fatal. An operational feed is
    edited by hand and mid-storm, and a provider that raises on one malformed
    line would report "no data" for a storm that is being tracked right now —
    which is the one situation where reporting a storm matters.

    Sorted because ATCF fixes are not guaranteed to arrive in order (the
    fetched sample carries several fixes sharing one timestamp), and a track
    drawn out of order is a scribble.
    """
    wanted = basin.strip().upper()
    waypoints: list[CycloneWaypoint] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.split(",")[0].strip().upper() != wanted:
            continue
        parsed = parse_atcf_line(line)
        if parsed is not None:
            waypoints.append(parsed)
    return tuple(sorted(waypoints, key=lambda w: w.iso_time))


def atcf_storm_id(fields: Iterable[str]) -> str | None:
    """The storm identifier for a record's fields, e.g. ``IO012026``.

    Built from the basin's own two-letter code and the storm number rather than
    from a field that does not exist, because ATCF has no separate id column.
    """
    parts = [part.strip() for part in fields]
    if len(parts) < 2:
        return None
    basin = parts[0].upper()
    number = parts[1].upper()
    if basin not in BASIN_CODES or not number:
        return None
    return f"{basin}{number}"