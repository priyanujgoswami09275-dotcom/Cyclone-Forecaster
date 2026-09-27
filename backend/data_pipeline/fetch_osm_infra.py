"""Fetch OSM infrastructure (hospitals, substations, roads) -> data/*.geojson.

Source: OpenStreetMap via the public Overpass API
    https://overpass-api.de/api/interpreter
Bbox: (21.30, 87.80, 22.60, 89.20) — South/North 24 Parganas + Sagar Island
(min_lat, min_lon, max_lat, max_lon).

IMPORTANT (Rules.md): never call Overpass live from a request handler —
this is a one-shot pre-fetch; the outputs are committed to /data. The
public instance rate-limits at ~2 concurrent requests/IP, so the three
queries here run strictly sequentially with a retry/backoff on 429.
"""

import json
import sys
import time
from pathlib import Path

import requests

# overpass-api.de rejects this environment's IP (406 for any query) and
# overpass.kumi.systems returns 504 on full-bbox queries from here.
# overpass.openstreetmap.fr is another official public mirror of the same
# Overpass service and answers these queries in ~10 s. Data source is
# unchanged: OpenStreetMap via Overpass.
OVERPASS_URL = "https://overpass.openstreetmap.fr/api/interpreter"
HEADERS = {"User-Agent": "cyclone-forecaster-hackathon/1.0 (OSM infra prefetch)"}
BBOX = "(21.30, 87.80, 22.60, 89.20)"  # south, west, north, east (Overpass bbox order)
OUT_DIR = Path(__file__).resolve().parents[2] / "data"

QUERIES = {
    "hospitals": f'[out:json][timeout:120];(nwr{BBOX}["amenity"~"hospital|clinic"];);out geom;',
    "substations": f'[out:json][timeout:120];(nwr{BBOX}["power"~"substation|plant"];);out geom;',
    "roads": (
        f'[out:json][timeout:120];(way{BBOX}["highway"~"motorway|trunk|primary|secondary"];);out geom;'
    ),
}


def overpass_to_geojson(elements: list[dict]) -> dict:
    """Overpass 'out geom' elements -> GeoJSON FeatureCollection.

    node -> Point; way -> LineString from its geometry array (skipped when
    geometry is absent — happens for partial/timeout responses).
    """
    features = []
    for el in elements:
        props = dict(el.get("tags") or {})
        props["osm_id"] = el["id"]
        if el["type"] == "node" and "lat" in el and "lon" in el:
            geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el["type"] == "way" and el.get("geometry"):
            geom = {
                "type": "LineString",
                "coordinates": [[p["lon"], p["lat"]] for p in el["geometry"]],
            }
        else:
            continue
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": features}


def fetch_overpass(query: str) -> list[dict]:
    """POST one query to Overpass; retries with backoff on 429/timeout/5xx."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=180)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()["elements"]
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(15)  # kumi throttles per-IP; wait out the rate window
    raise RuntimeError(f"Overpass query failed after retries: {last_exc}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, query in QUERIES.items():
        elements = fetch_overpass(query)
        if not elements:
            sys.exit(f"Overpass returned 0 elements for '{name}' — refusing to write empty file.")
        fc = overpass_to_geojson(elements)
        out = OUT_DIR / f"{name}.geojson"
        with open(out, "w") as f:
            json.dump(fc, f, indent=2)
        print(f"{name}: {len(fc['features'])} features -> {out}")


if __name__ == "__main__":
    main()
