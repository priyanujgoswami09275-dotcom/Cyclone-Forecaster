"""Fetch Cyclone Remal's IBTrACS track and write data/remal_track.geojson.

Source: NOAA IBTrACS v04r00, North Indian Ocean basin CSV.
    https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship/v04r00/access/csv/ibtracs.NI.list.v04r00.csv
Filter: NAME == "REMAL", SEASON == "2024". Row index 1 of the file is a
units row (e.g. LAT unit "deg_north") and is dropped by the NAME filter.

Unit note: IBTrACS USA_WIND is in KNOTS. The surge training table in
CLAUDE.md uses km/h. This script intentionally does NOT convert — track
values keep their source units and the GeoJSON properties say so.
"""

import csv
import io
import json
import sys
from pathlib import Path

import requests

IBTRACS_URL = (
    # NOAA moved the dataset to the "...-ibtracs" suffix; the v04r00 path in
    # CLAUDE.md now 404s. Same dataset, same version, current base path.
    "https://www.ncei.noaa.gov/data/"
    "international-best-track-archive-for-climate-stewardship-ibtracs/"
    "v04r00/access/csv/ibtracs.NI.list.v04r00.csv"
)
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "remal_track.geojson"


def parse_ibtracs_rows(csv_text: str) -> list[dict]:
    """Parse IBTrACS CSV text -> REMAL 2024 rows (dicts of iso_time/lat/lon/usa_wind_kt)."""
    rows = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        if row.get("NAME") != "REMAL" or row.get("SEASON") != "2024":
            continue
        if not row.get("LAT") or not row.get("LON"):
            continue  # missing position — unusable in the track
        rows.append(
            {
                "iso_time": row["ISO_TIME"],
                "lat": float(row["LAT"]),
                "lon": float(row["LON"]),
                # USA_WIND may be blank (space-padded) at weak stages
                "usa_wind_kt": float(row["USA_WIND"]) if row.get("USA_WIND") else 0.0,
            }
        )
    rows.sort(key=lambda r: r["iso_time"])
    return rows


def track_to_geojson(rows: list[dict]) -> dict:
    """Rows -> FeatureCollection: one LineString (time-ordered) + one Point per fix."""
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[r["lon"], r["lat"]] for r in rows],
            },
            "properties": {
                "name": "REMAL",
                "season": "2024",
                "source": "IBTrACS v04r00",
                "wind_units": "knots",
            },
        }
    ]
    for r in rows:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {"iso_time": r["iso_time"], "usa_wind_kt": r["usa_wind_kt"]},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    resp = requests.get(IBTRACS_URL, timeout=120)
    resp.raise_for_status()  # a 404 page parses as "no rows" — fail loudly instead
    rows = parse_ibtracs_rows(resp.text)
    if not rows:
        sys.exit("No REMAL 2024 rows found in IBTrACS — check NAME/SEASON filter.")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(track_to_geojson(rows), f, indent=2)
    max_wind = max(r["usa_wind_kt"] for r in rows)
    print(
        f"Wrote {OUT_PATH}: {len(rows)} fixes, "
        f"{rows[0]['iso_time']} -> {rows[-1]['iso_time']}, max USA_WIND {max_wind:.0f} kt"
    )


if __name__ == "__main__":
    main()
