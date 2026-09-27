"""Fetch OSM place nodes -> data/places.geojson (locality list for the API).

Why: Module C's `/routes?origin={block_id}` needs a stable id -> coordinate
mapping, and `/allocation` needs per-locality demand nodes whose names appear
verbatim in the Gemini advisory ("evacuate Kakdwip first"). Both need real
named places at real coordinates.

Hand-typing coordinates from memory was not an option: Rules.md requires
every figure in the output to trace to a real named source, and a locality
centroid invented from recollection would be a fabricated number wearing a
real place's name — the exact failure mode the shelter work already had to
refuse once. So the names and coordinates come from OpenStreetMap, committed,
and served statically (Rules.md: never from a request handler).

Scope is deliberately `city|town|village|suburb|hamlet` — the settled
settlement hierarchy of the delta. `isolated_dwelling` is excluded because it
is a building-level tag, not a locality, and it would swamp the list.

Refresh with:
    venv/bin/python backend/data_pipeline/fetch_osm_places.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

# Same mirror rationale as the other fetch scripts in this directory.
OVERPASS_URL = "https://overpass.openstreetmap.fr/api/interpreter"
HEADERS = {"User-Agent": "cyclone-forecaster-hackathon/1.0 (OSM places prefetch)"}
BBOX = "(21.30, 87.80, 22.60, 89.20)"  # south, west, north, east

QUERY = (
    f'[out:json][timeout:180];'
    f'(node{BBOX}["place"~"city|town|village|suburb|hamlet"];);'
    f"out;"
)

OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "places.geojson"


def fetch() -> list[dict]:
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                OVERPASS_URL, data={"data": QUERY}, headers=HEADERS, timeout=180
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()["elements"]
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(15)
    raise RuntimeError(f"Overpass query failed after retries: {last_exc}")


def main() -> None:
    elements = fetch()
    if not elements:
        sys.exit("Overpass returned 0 places — refusing to write an empty file.")

    features = []
    for el in elements:
        if "lat" not in el or "lon" not in el:
            continue
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        props = {
            "osm_id": el["id"],
            "name": name,
            "place": tags.get("place"),
            # OSM's population tag is crowd-sourced and often absent or stale.
        }
        # Carried through only when present, and never used as an authority —
        # the API's own figures come from population.estimate_populations().
        if tags.get("population"):
            props["population_tag"] = tags["population"]
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [el["lon"], el["lat"]],
                },
                "properties": props,
            }
        )

    fc = {"type": "FeatureCollection", "features": features}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(fc, f, indent=2)

    counts: dict[str, int] = {}
    for f in features:
        counts[f["properties"]["place"]] = counts.get(f["properties"]["place"], 0) + 1
    print(f"places: {len(features)} features -> {OUT_PATH}")
    print("  by place type:", counts)


if __name__ == "__main__":
    main()
