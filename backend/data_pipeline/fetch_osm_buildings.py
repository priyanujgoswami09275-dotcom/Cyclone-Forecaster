"""Fetch OSM building footprint CENTROIDS -> data/buildings.csv.gz.

Why this exists: `backend/simulation/population.py` estimates exposed
population from OSM building density, and the committed extract had no
buildings in it — only hospitals, substations and roads. Module C's
/allocation endpoint needs a per-locality evacuation demand, and inventing
that number was not an option (Rules.md: an estimate must be labelled as one,
and a fabricated one is worse than none).

**Why centroids, not polygons.** population.py takes `buildings` as a list of
(lon, lat) points and counts those inside each locality's extent. The bbox
holds ~661,000 buildings; as GeoJSON Polygons that is hundreds of megabytes and
has no business in git. Centroids are what the density calculation consumes
anyway, so that is what is stored.

**Why CSV, not GeoJSON.** The Overpass CSV writer emits just the two
coordinates per row (~13 MB) instead of full element JSON with tags
(~250 MB). Committed gzipped it is a few MB, and it loads in well under a
second.

**What this file is and is not.** It is real, traceable OpenStreetMap data
(ODbL), a pre-fetch — never called from a request handler (Rules.md). It is
*not* a population dataset: persons-per-building is still an assumption
(`population.POP_PER_BUILDING`). The estimate remains an estimate, and is
labelled as one wherever it surfaces.

Refresh with:
    venv/bin/python backend/data_pipeline/fetch_osm_buildings.py
"""

from __future__ import annotations

import csv
import gzip
import sys
import time
from pathlib import Path

import requests

# Same mirror rationale as fetch_osm_infra.py: overpass-api.de rejects this
# environment's IP; overpass.openstreetmap.fr is an official public mirror of
# the same service.
OVERPASS_URL = "https://overpass.openstreetmap.fr/api/interpreter"
HEADERS = {"User-Agent": "cyclone-forecaster-hackathon/1.0 (OSM buildings prefetch)"}
BBOX = "(21.30, 87.80, 22.60, 89.20)"  # south, west, north, east — the study bbox

# ::lat/::lon resolve to a way's centroid because of `out center`; for a node
# they are the node itself. No id, no tags — only what the density needs.
# The csv writer's default separator is a tab; the optional separator argument
# is rejected by this mirror's parser, so it is left at the default.
QUERY = (
    f'[out:csv(::lat,::lon;false)][timeout:300];'
    f'(way{BBOX}["building"];node{BBOX}["building"];);'
    f"out center;"
)

OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "buildings.csv.gz"


def fetch() -> list[tuple[float, float]]:
    """All building centroids in the bbox as (lon, lat). Retries on 429/5xx."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                OVERPASS_URL, data={"data": QUERY}, headers=HEADERS, timeout=300
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return _parse_csv(resp.text)
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(20)
    raise RuntimeError(f"Overpass query failed after retries: {last_exc}")


def _parse_csv(text: str) -> list[tuple[float, float]]:
    # Overpass's csv writer separates fields with a TAB, not a comma — the
    # default csv.reader delimiter silently yields one field per line here.
    rows = []
    for record in csv.reader(text.splitlines(), delimiter="\t"):
        if len(record) != 2:
            continue
        try:
            lat, lon = float(record[0]), float(record[1])
        except ValueError:
            continue  # header row, or a blank/partial line
        if lat == 0.0 and lon == 0.0:
            continue  # Overpass emits 0,0 for elements it could not centre
        rows.append((lon, lat))
    return rows


def main() -> None:
    points = fetch()
    if not points:
        sys.exit("Overpass returned 0 buildings — refusing to write an empty file.")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT_PATH, "wt", compresslevel=9) as fh:
        writer = csv.writer(fh)
        writer.writerow(["lon", "lat"])
        writer.writerows(points)

    size_mb = OUT_PATH.stat().st_size / 1e6
    print(
        f"buildings: {len(points)} centroids -> {OUT_PATH} "
        f"({size_mb:.1f} MB gzipped)"
    )


if __name__ == "__main__":
    main()
