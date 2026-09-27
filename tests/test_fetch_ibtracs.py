"""Tests for backend/data_pipeline/fetch_ibtracs.py — REMAL 2024 track parsing."""
from backend.data_pipeline.fetch_ibtracs import parse_ibtracs_rows, track_to_geojson

# Minimal but faithful IBTrACS v04r00 CSV layout:
# header row, units row (row index 1 — 'kt'/'deg_north' placeholders), then data.
FIXTURE_CSV = """SID,SEASON,NUMBER,BASIN,SUBBASIN,NAME,ISO_TIME,NATURE,LAT,LON,USA_WIND
,unit,,,,,,,deg_north,deg_west,kt
1983145N10088,1983,1,NI,B,REMAL,1983-10-07 00:00:00,TS,10.1,88.2,45
2024143N16088,2024,2,NI,B,REMAL,2024-05-26 12:00:00,TS,21.8,88.9,65
2024143N16088,2024,2,NI,B,REMAL,2024-05-24 00:00:00,DS,16.5,87.9,35
2024143N16088,2024,2,NI,B,REMAL,2024-05-25 12:00:00,TS,19.2,88.0,55
"""


# Real IBTrACS pads fields with spaces; a blank USA_WIND arrives as " ".
FIXTURE_CSV_PADDED = """SID,SEASON,NUMBER,BASIN,SUBBASIN,NAME,ISO_TIME,NATURE,LAT,LON,USA_WIND
2024143N16088, 2024, 2, NI, B, REMAL, 2024-05-26 12:00:00, TS, 21.8, 88.9,
"""


def test_parse_handles_space_padded_fields_and_blank_wind():
    rows = parse_ibtracs_rows(FIXTURE_CSV_PADDED)
    assert len(rows) == 1
    assert rows[0]["usa_wind_kt"] == 0.0
    assert rows[0]["lat"] == 21.8



def test_parse_filters_remal_2024_and_skips_units_row():
    rows = parse_ibtracs_rows(FIXTURE_CSV)
    assert all(r["iso_time"].startswith("2024") for r in rows)
    assert len(rows) == 3
    assert rows[0]["usa_wind_kt"] != "kt"  # units row leaked -> str, not float
    assert isinstance(rows[0]["usa_wind_kt"], float)
    assert isinstance(rows[0]["lat"], float) and isinstance(rows[0]["lon"], float)


def test_parse_returns_rows_in_time_order():
    rows = parse_ibtracs_rows(FIXTURE_CSV)
    times = [r["iso_time"] for r in rows]
    assert times == sorted(times)


def test_track_to_geojson_structure():
    fc = track_to_geojson(parse_ibtracs_rows(FIXTURE_CSV))
    types = sorted(f["geometry"]["type"] for f in fc["features"])
    assert types == ["LineString"] + ["Point"] * 3
    line = next(f for f in fc["features"] if f["geometry"]["type"] == "LineString")
    assert line["properties"]["name"] == "REMAL"
    assert line["properties"]["season"] == "2024"
    assert line["properties"]["wind_units"] == "knots"
    # LineString coordinates follow time order: [lon, lat] pairs
    assert line["geometry"]["coordinates"][0] == [87.9, 16.5]
    assert line["geometry"]["coordinates"][-1] == [88.9, 21.8]
