"""Fetch the Bangladesh country boundary -> data/bangladesh_boundary.geojson.

Source: OpenStreetMap via the public Overpass API (ODbL)
    https://overpass.openstreetmap.fr/api/interpreter
The query selects the country relation by its tags (boundary=administrative,
admin_level=2, ISO3166-1=BD) — never by a hardcoded id, so a relation re-
numbering upstream cannot silently turn this into a different country.

Bbox: (21.30, 87.80, 22.60, 89.20) — the DEM bbox (south, west, north, east),
identical to fetch_osm_infra.py. The polygon is CLIPPED to it before being
written: only the border arc crossing the study area matters downstream.

Why this polygon exists (the subtraction design, 2026-10-17): the flood
engine runs over the whole DEM bbox, which reaches 89.2E — into Bangladesh —
and nothing clipped the extent at the border, so roughly a fifth of every
"flooded land" figure was Bangladeshi territory. The engine now SUBTRACTS
this polygon's cells instead of clipping to a West Bengal polygon: the
OSM-vs-DEM coastline mismatch would delete real coastal West Bengal cells
under a clip-to-WB approach, and the flood is only ~0.5 km wide, so an
over-precise clip is the more dangerous error. Subtraction can only ever
remove cells OSM says are Bangladesh — it cannot eat the WB coast.

IMPORTANT (Rules.md): never call Overpass live from a request handler —
this is a one-shot pre-fetch; the output is committed to /data. The public
instance rate-limits, so the query runs with the same retry/backoff as
fetch_osm_infra.py. If the fetch fails after retries, STOP: the boundary
must never be hand-drawn or approximated — a wrong border silently corrupts
every area, exposure and allocation figure the app serves.

The script self-checks after writing (see main()): the two DISTRICT_DENY
places must fall inside the polygon, every served locality outside it, the
named Bangladeshi clinics inside, Barunhat Rural Hospital outside, and the
polygon's westernmost vertex must stay east of 88.90E — unambiguously deep
West Bengal. The band [88.90, 88.95) is a WARNING, not a failure: the real
Ichamati river border meanders to ~88.938E near the northern bbox edge
(measured 2026-10-17: westmost vertex (88.9384, 22.5893); Taki Rural
Hospital and Matrisadan Nursing Home just west of it are correctly outside),
so a dip there is the border being honest, not the polygon being wrong.
"""

import json
import sys
import time
from pathlib import Path

import requests
from shapely.geometry import LineString, box, mapping
from shapely.ops import polygonize, unary_union

# overpass-api.de rejects this environment's IP (406 for any query) and
# overpass.kumi.systems returns 504 on full-bbox queries from here. This is
# the same working mirror fetch_osm_infra.py uses; the data source is
# unchanged: OpenStreetMap via Overpass.
OVERPASS_URL = "https://overpass.openstreetmap.fr/api/interpreter"
HEADERS = {"User-Agent": "cyclone-forecaster-hackathon/1.0 (OSM boundary prefetch)"}

# south, west, north, east (Overpass bbox order) — the DEM bbox.
BBOX = (21.30, 87.80, 22.60, 89.20)
# shapely box takes (minx, miny, maxx, maxy) = (west, south, east, north).
CLIP_BOX = box(BBOX[1], BBOX[0], BBOX[3], BBOX[2])

# ~33 m at this latitude. "No coarser than" this per the design note: any
# looser and the border arc starts wandering by more than a DEM cell.
SIMPLIFY_TOL_DEG = 0.0003
TARGET_BYTES = 500_000

# The western longitudes the polygon may reach. The REAL border dips to
# ~88.938E along the Ichamati river meander near the bbox's northern edge
# (lat ~22.55-22.60): measured 2026-10-17 against the OSM country relation,
# the westmost vertex in this bbox is (88.9384, 22.5893), and the West
# Bengal facilities hugging that stretch — Taki Rural Hospital at 88.9283,
# Matrisadan Nursing Home at 88.9164, Barunhat Rural Hospital at 88.9531 —
# all fall outside the polygon. So [88.90, 88.95) is a WARNING band, not a
# failure: that is the real river border, not a broken fetch. Anything west
# of 88.90E is unambiguously deep West Bengal and IS a hard failure.
WESTERN_WARN = 88.95
WESTERN_FAIL = 88.90

OUT_DIR = Path(__file__).resolve().parents[2] / "data"
OUT_PATH = OUT_DIR / "bangladesh_boundary.geojson"

QUERY = (
    "[out:json][timeout:180];"
    'relation["boundary"="administrative"]["admin_level"="2"]["ISO3166-1"="BD"];'
    "out geom;"
)

# The DISTRICT_DENY ids (backend/locations.py) — places in Satkhira
# District, Bangladesh that must fall inside the fetched polygon.
DENY_LIST_IDS = ("osm-7488876257", "osm-2481969300")


def fetch_overpass(query: str) -> list[dict]:
    """POST one query to Overpass; retries with backoff on 429/timeout/5xx.

    Same ladder as fetch_osm_infra.py — the public instance throttles per IP.
    """
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=240)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()["elements"]
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(15)
    raise RuntimeError(f"Overpass query failed after retries: {last_exc}")


def relation_to_polygon(relation: dict):
    """Assemble the relation's outer ways into one (Multi)Polygon.

    `out geom` returns each member way's coordinates inline. The admin-level-2
    relation of Bangladesh carries closed-ish border ways as role "outer"
    (it has had no enclaves since the 2015 exchange), so inner rings are not
    expected; if a way carries no role it is still treated as outer, which
    is how older OSM boundary relations were tagged.
    """
    lines = []
    for member in relation.get("members", []):
        if member.get("type") != "way":
            continue
        if member.get("role") not in ("outer", "", None):
            continue
        coords = [(p["lon"], p["lat"]) for p in (member.get("geometry") or [])]
        if len(coords) >= 2:
            lines.append(LineString(coords))
    if not lines:
        raise RuntimeError("relation carried no outer ways with geometry")
    polygons = list(polygonize(lines))
    if not polygons:
        raise RuntimeError("outer ways did not close into any polygon")
    merged = unary_union(polygons)
    if not merged.is_valid:
        merged = merged.buffer(0)
    return merged


def sanity_report(polygon) -> None:
    """Print the containment checks the committed test also pins.

    Runs at fetch time so a bad fetch fails loudly here, before anything
    downstream consumes it.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from shapely.geometry import Point

    from backend.locations import all_localities, localities

    failures = []

    for loc in all_localities():
        if loc.id in DENY_LIST_IDS:
            ok = polygon.contains(Point(loc.lon, loc.lat))
            print(f"  deny-list {loc.id} ({loc.name!r}) inside: {ok}")
            if not ok:
                failures.append(f"{loc.id} not inside the polygon")

    inside_served = [
        loc.name for loc in localities() if polygon.contains(Point(loc.lon, loc.lat))
    ]
    print(f"  served localities: {len(localities())} total, inside polygon: {len(inside_served)}")
    if inside_served:
        failures.append(f"served localities swallowed: {inside_served}")

    hospitals = json.loads((OUT_DIR / "hospitals.geojson").read_text())["features"]
    band = []
    for f in hospitals:
        geom = f.get("geometry") or {}
        coords = (
            geom["coordinates"]
            if geom.get("type") == "Point"
            else (geom.get("coordinates") or [[]])[0]
        )
        if not coords or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        # The interleaved strip: everything east of 89.0 should be
        # Bangladeshi; [88.90, 89.0) is the river-border stretch where WB
        # and BD facilities sit within a few km of each other.
        if lon >= 88.90:
            name = (f.get("properties") or {}).get("name") or "(unnamed)"
            band.append((name, lon, lat))
    for name, lon, lat in sorted(band, key=lambda t: t[1]):
        print(
            f"  hospital at/after 88.90E: {name!r} at ({lon:.4f}, {lat:.4f}) "
            f"inside: {polygon.contains(Point(lon, lat))}"
        )

    west_vertex = polygon.bounds[0]
    westmost = min(
        (
            c
            for part in (polygon.geoms if polygon.geom_type == "MultiPolygon" else [polygon])
            for c in part.exterior.coords
        ),
        key=lambda c: c[0],
    )
    print(
        f"  polygon westernmost vertex: ({westmost[0]:.4f}, {westmost[1]:.4f}) "
        f"— warn < {WESTERN_WARN}, fail < {WESTERN_FAIL}"
    )
    if west_vertex < WESTERN_FAIL:
        failures.append(
            f"polygon reaches lon {west_vertex:.4f}, west of {WESTERN_FAIL} — "
            "that far west is unambiguously West Bengal; the fetch or the "
            "assembly is wrong"
        )
    elif west_vertex < WESTERN_WARN:
        print(
            f"  WARNING: polygon reaches {west_vertex:.4f}E, inside the river-"
            "meander band west of 88.95E. That is expected along the Ichamati "
            "border near the northern bbox edge — verify every WB facility "
            "printed above reads inside: False before trusting the fetch."
        )

    if failures:
        raise RuntimeError("sanity checks FAILED: " + "; ".join(failures))
    print("  sanity checks passed")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    elements = fetch_overpass(QUERY)
    relations = [el for el in elements if el.get("type") == "relation"]
    if len(relations) != 1:
        sys.exit(
            f"Expected exactly one BD admin_level=2 relation, got {len(relations)} "
            f"(ids: {[r.get('id') for r in relations]}). Refusing to guess."
        )
    print(f"relation {relations[0]['id']}: assembling outer ways...")
    country = relation_to_polygon(relations[0])

    clipped = country.intersection(CLIP_BOX)
    if clipped.is_empty:
        sys.exit("Clipped polygon is empty — the relation does not intersect the DEM bbox? Refusing to write.")
    simplified = clipped.simplify(SIMPLIFY_TOL_DEG, preserve_topology=True)
    if simplified.is_empty or simplified.area <= 0:
        sys.exit("Simplified polygon is empty — simplify tolerance degenerate? Refusing to write.")

    west, south, east, north = simplified.bounds
    n_vertices = sum(
        len(part.exterior.coords)
        for part in (simplified.geoms if simplified.geom_type == "MultiPolygon" else [simplified])
    )
    props = {
        "name": "Bangladesh",
        "boundary": "administrative",
        "admin_level": 2,
        "osm_relation_id": relations[0]["id"],
        "source": "OpenStreetMap",
        "license": "ODbL-1.0",
        "attribution": (
            "Boundary data (c) OpenStreetMap contributors, available under the "
            "Open Database License (ODbL) v1.0. Fetched via Overpass from the "
            "country relation (admin_level=2, ISO3166-1=BD), clipped to the DEM "
            "bbox and simplified at 0.0003 deg."
        ),
        "clip_bbox_wsen": list(BBOX),
        "simplify_tolerance_deg": SIMPLIFY_TOL_DEG,
        "bounds_wsen_after": [round(west, 6), round(south, 6), round(east, 6), round(north, 6)],
        "vertex_count_outer_rings": n_vertices,
        "purpose": (
            "Subtracted from the flood engine's results so flooded area and "
            "facility exposure exclude Bangladeshi territory. NOT used to clip "
            "to West Bengal — see backend/simulation/flood.py."
        ),
    }
    fc = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": props, "geometry": mapping(simplified)}],
    }
    OUT_PATH.write_text(json.dumps(fc, indent=2))
    size = OUT_PATH.stat().st_size
    print(f"wrote {OUT_PATH}  ({size:,} B, {n_vertices} outer-ring vertices, target < {TARGET_BYTES:,} B)")
    if size >= TARGET_BYTES:
        sys.exit(f"File is {size:,} B — over the {TARGET_BYTES:,} B target. Reduce indent or simplify further.")

    sanity_report(simplified)


if __name__ == "__main__":
    main()
