"""Localities and building centroids: the demand nodes and route origins.

Module C needs two things that no Module B module owned:

1. **An id -> coordinate mapping** for `/routes?origin={block_id}`, and
2. **Per-locality evacuation demand** for `/allocation`.

Both come from committed OpenStreetMap extracts, never from a live query
(Rules.md). Place names and coordinates are `data/places.geojson`; building
centroids are `data/buildings.csv.gz` (660,893 of them). Neither is invented:
typing a locality's coordinates from recollection would put a fabricated
number under a real place's name, which is the failure the shelter work
already had to refuse once.

**Which localities.** Every `city`, `town` and `village` in the extract (73
places — Diamond Harbour, Kakdwip, Canning, Haldia, Tamluk, the delta
villages), plus four settlements that OSM tags as `suburb` but which the
case study names directly: Patharpratima, Namkhana, Gosaba and Sagar Island.
Those four are added explicitly and the reason is recorded in
`DELTA_PLACES_ADDED` rather than being silently mixed in.

**`radius_km` is a search radius, not a boundary.** It is how far around the
centroid the density estimate counts buildings; it is emphatically NOT a
claim about where a district or block ends. Administrative boundaries for
these blocks are not in the dataset and are not inferred from a circle.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
PLACES_PATH = DATA_DIR / "places.geojson"
BUILDINGS_PATH = DATA_DIR / "buildings.csv.gz"

# OSM place tiers used as localities, in the settled settlement hierarchy.
INCLUDED_PLACE_TYPES = ("city", "town", "village")

# Search radius for the building-density estimate, by tier. A city has a
# denser, wider footprint than a village; these are ordering choices, and the
# estimate that comes out is labelled an estimate regardless.
RADIUS_KM_BY_TYPE = {"city": 12.0, "town": 8.0, "village": 4.0, "suburb": 6.0}

# **Study area.** The DEM bbox (21.30-22.60 N) clips the northern edge of the
# Kolkata urban agglomeration, whose dense 12 km-radius density boxes produce
# ~90% of the raw demand total — Bidhannagar 93k, New Town 68k, Kolkata 51k.
# Those are other districts' suburbs that the bounding box happened to clip,
# not the delta the case study is about, and including them buried the towns
# the narrative actually names (Kakdwip, Patharpratima, Gosaba).
#
# So the study area is South 24 Parganas and the delta, taken as everything
# south of this latitude. It is a scoping decision, NOT a boundary claim: the
# real South 24 Parganas district boundary is not in the dataset, and this
# line is an approximation of it. See MEMORY.md "Flagged for review".
STUDY_AREA_MAX_LAT = 22.40

# Settlements the case study names that OSM classifies as `suburb`. Without
# these the delta towns in the narrative would be missing from the API's
# locality list while lesser places were present.
DELTA_PLACES_ADDED = (
    "Patharpratima",  # southern Sagar Island block HQ
    "Namkhana",       # cyclone-shelter block on the delta
    "Gosaba",         # delta block
    "Sagar",          # the island itself — the case study's landfall
)


@dataclass(frozen=True)
class Locality:
    id: str
    name: str
    lon: float
    lat: float
    radius_km: float
    place: str
    source: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lon": self.lon,
            "lat": self.lat,
            "radius_km": self.radius_km,
            "place": self.place,
            "source": self.source,
        }


def _slug(name: str) -> str:
    """Stable, URL-safe id from a place name.

    Non-Latin names (the delta has Bengali-script entries) transliterate to
    an empty slug, so those fall back to the OSM id — still stable, and the
    display name is carried separately either way.
    """
    ascii_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return ascii_name


@lru_cache(maxsize=1)
def all_localities() -> tuple[Locality, ...]:
    """Every locality in the committed extract, before study-area scoping."""
    if not PLACES_PATH.exists():
        raise FileNotFoundError(
            f"{PLACES_PATH} not found. Pre-fetched and committed by design "
            "(Rules.md: no live fetches from a request handler) — regenerate "
            "with backend/data_pipeline/fetch_osm_places.py."
        )
    features = json.loads(PLACES_PATH.read_text())["features"]

    selected: list[Locality] = []
    seen_ids: set[str] = set()
    for feature in features:
        props = feature["properties"]
        place = props.get("place")
        name = props["name"]
        if place not in INCLUDED_PLACE_TYPES and name not in DELTA_PLACES_ADDED:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        base = _slug(name) or f"osm-{props['osm_id']}"
        # Two places can share a name (Basudebpur appears twice); the OSM id
        # keeps ids unique and stable across reloads.
        locality_id = base if base not in seen_ids else f"{base}-{props['osm_id']}"
        seen_ids.add(locality_id)
        selected.append(
            Locality(
                id=locality_id,
                name=name,
                lon=float(lon),
                lat=float(lat),
                radius_km=RADIUS_KM_BY_TYPE.get(place, 6.0),
                place=place,
                source="OpenStreetMap place node (data/places.geojson)",
            )
        )

    if not selected:
        raise ValueError("No localities found in data/places.geojson")
    return tuple(sorted(selected, key=lambda loc: loc.name))


def in_study_area(locality: Locality) -> bool:
    return locality.lat <= STUDY_AREA_MAX_LAT


@lru_cache(maxsize=1)
def localities() -> tuple[Locality, ...]:
    """The study area's localities — what the API serves.

    `all_localities()` minus everything north of STUDY_AREA_MAX_LAT. Kept
    available so a caller can see what was excluded and why rather than
    finding a locality silently missing.
    """
    return tuple(loc for loc in all_localities() if in_study_area(loc))


@lru_cache(maxsize=1)
def scoping() -> dict:
    """Provenance for the study-area cut, shipped with /localities."""
    in_area = len(localities())
    excluded = [loc for loc in all_localities() if not in_study_area(loc)]
    return {
        "study_area_max_lat": STUDY_AREA_MAX_LAT,
        "localities_in_study_area": in_area,
        "localities_excluded": len(excluded),
        "excluded_names": sorted(loc.name for loc in excluded),
        "disclosure": (
            f"Scoped to lat <= {STUDY_AREA_MAX_LAT} (South 24 Parganas and the "
            "delta). The DEM bbox clips the northern Kolkata suburbs, whose "
            "density estimates would otherwise dominate the district totals. "
            "This line approximates the district boundary — it is not the "
            "boundary, which is not in the dataset."
        ),
    }


def get_locality(locality_id: str) -> Locality | None:
    for locality in localities():
        if locality.id == locality_id:
            return locality
    return None


@lru_cache(maxsize=1)
def _building_arrays() -> tuple[np.ndarray, np.ndarray]:
    """(lons, lats) of every mapped building centroid, as float arrays."""
    if not BUILDINGS_PATH.exists():
        raise FileNotFoundError(
            f"{BUILDINGS_PATH} not found. Pre-fetched and committed by design "
            "(Rules.md) — regenerate with "
            "backend/data_pipeline/fetch_osm_buildings.py."
        )
    lons: list[float] = []
    lats: list[float] = []
    with gzip.open(BUILDINGS_PATH, "rt") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header
        for record in reader:
            if len(record) != 2:
                continue
            lons.append(float(record[0]))
            lats.append(float(record[1]))
    return np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)


@lru_cache(maxsize=1)
def building_count() -> int:
    lons, _ = _building_arrays()
    return int(lons.size)


def buildings_near(locality: Locality) -> list[tuple[float, float]]:
    """Building centroids inside a locality's search radius.

    A numpy bounding-box pre-filter, because `estimate_populations` counts
    with a Python loop over the list it is given: handing it all 660,893
    centroids for each of ~77 localities would be ~50M shapely calls. The
    bounding box is a superset of the circular extent, so the exact
    `extent.contains` test inside `estimate_populations` still decides what
    counts — this only narrows the candidates, it does not change the result.
    """
    lons, lats = _building_arrays()
    dlat = locality.radius_km / 110.574
    dlon = locality.radius_km / (111.320 * math.cos(math.radians(locality.lat)))
    mask = (
        (lats >= locality.lat - dlat)
        & (lats <= locality.lat + dlat)
        & (lons >= locality.lon - dlon)
        & (lons <= locality.lon + dlon)
    )
    return list(zip(lons[mask].tolist(), lats[mask].tolist()))


def population_methodology() -> dict:
    """Provenance for the demand figures, for the API's disclosure blocks."""
    from .simulation.population import methodology

    info = methodology()
    info["building_centroids"] = building_count()
    info["building_source"] = (
        "OpenStreetMap building footprints, centroids only "
        "(data/buildings.csv.gz, pre-fetched)"
    )
    info["localities"] = len(localities())
    info["scoping"] = scoping()
    return info
