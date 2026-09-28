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
places — Diamond Harbour, Kakdwip, Canning, Haldia, the delta villages), plus
four settlements that OSM tags as `suburb` but which the case study names
directly: Patharpratima, Namkhana, Gosaba and Sagar Island. Those four are
added explicitly and the reason is recorded in `DELTA_PLACES_ADDED` rather
than being silently mixed in.

**Which districts.** `DISTRICT_DENY` names the places in the extract that are
not in South/North 24 Parganas or Sagar Island, and `BORDER_CLUSTER` names the
ones the dataset cannot settle. The extract has no `admin_level` tags and no
boundary geometry, so membership is a stated decision, not a computed fact.

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
# So the study area is South 24 Parganas, the delta, and the southern sliver of
# North 24 Parganas that the same bbox catches (Sandeshkhali, Shyamnagar).
STUDY_AREA_DISTRICTS = ("South 24 Parganas", "North 24 Parganas", "Sagar Island")

# **Explicit out-of-district exclusions.** The extract carries no
# `admin_level` tags and no boundary geometry, so membership cannot be computed
# — it has to be named. This is the authoritative list, and the value is the
# district the place really belongs to, so the list is self-describing and
# says *why* something is absent rather than leaving a silent hole.
#
# Tamluk is the case that forced this: it is in Purba Medinipur, but it sits
# inside the bbox and south of the northern cut, so it survived scoping, landed
# in `/allocation`, and reached the Gemini prompt as a place needing evacuation
# on South 24 Parganas advice. See MEMORY.md #17/#24.
#
# Adding a locality is one line. Do not add one "just in case" — every entry
# here removes a real place from a real advisory, and the ones listed in
# `BORDER_CLUSTER` below are genuinely undecided rather than confirmed.
DISTRICT_DENY: dict[str, str] = {
    "Tamluk": "Purba Medinipur (Haldia subdivision)",
}

# **Unresolved, deliberately NOT denied.** `data/places.geojson` has no
# administrative tags, so for these the district could not be confirmed from
# the dataset and is not something this code should guess. They sit on or near
# the western South 24 Parganas / Purba Medinipur boundary, which the DEM bbox
# is the only thing clipping. They are kept in the study area and reported
# here, because an admission that a boundary is uncertain is worth more than a
# confident wrong answer — and because dropping a real delta village on a hunch
# is the worse error. A human with a boundary dataset should settle these.
BORDER_CLUSTER: tuple[str, ...] = (
    "Anantapur",   # 22.32 N, 87.96 E — ~6 km east of Tamluk, same band
    "Nandakumar",  # 22.20 N, 87.92 E — 8 km south of Tamluk
    "Syampur",     # 22.30 N, 88.03 E — same latitude as Tamluk, 1.2 km east
    "Bajkul",      # 22.02 N, 87.82 E — 2.6 km inside the bbox's western edge
    "Dholmari",    # 21.81 N, 87.83 E — 3.1 km inside the western edge
    "Basantia",    # 21.80 N, 87.81 E — 1.1 km inside the western edge
    "Junput",      # 21.73 N, 87.81 E — 1.1 km inside the western edge
    "Henria",      # 21.97 N, 87.80 E — on the western edge itself
)

# **The northern cut.** This is NOT the district boundary and never claimed to
# be; it is a coarse guard whose one job is to keep the clipped Kolkata suburbs
# out of the demand totals. Actual district membership is `DISTRICT_DENY`;
# anything south of this line that is in the wrong district has to be named
# there, because this line cannot detect it.
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
    """Whether `locality` belongs in the study area.

    District membership first, the northern cut second. The order matters: the
    cut cannot detect a wrong-district place south of the line, so a name in
    `DISTRICT_DENY` has to be rejected regardless of where it falls.
    """
    if locality.name in DISTRICT_DENY:
        return False
    return locality.lat <= STUDY_AREA_MAX_LAT


def _exclusion_reason(locality: Locality) -> str:
    if locality.name in DISTRICT_DENY:
        return f"out of district: {DISTRICT_DENY[locality.name]}"
    return f"north of the {STUDY_AREA_MAX_LAT} study-area cut"


@lru_cache(maxsize=1)
def _deny_list_audit() -> dict:
    """Catch a `DISTRICT_DENY` entry that does nothing.

    A typo in a deny-list key fails open: the place is still served, still
    reaches the prompt, and the list looks like it is working. Cheap to check,
    so it is checked rather than trusted.
    """
    present = {loc.name for loc in all_localities()}
    return {
        "not_in_extract": sorted(set(DISTRICT_DENY) - present),
        "excluded": sorted(name for name in DISTRICT_DENY if name in present),
    }


@lru_cache(maxsize=1)
def localities() -> tuple[Locality, ...]:
    """The study area's localities — what the API serves.

    `all_localities()` minus the explicitly-denied out-of-district places and
    everything north of STUDY_AREA_MAX_LAT. Kept available so a caller can see
    what was excluded and why rather than finding a locality silently missing.
    """
    return tuple(loc for loc in all_localities() if in_study_area(loc))


@lru_cache(maxsize=1)
def scoping() -> dict:
    """Provenance for the study-area scoping, shipped with /localities.

    The two exclusion mechanisms are reported separately, because they are not
    the same kind of claim: `DISTRICT_DENY` is a decision we made and can name,
    while the latitude cut is a coarse guard standing in for a boundary that
    the dataset does not contain.
    """
    in_area = len(localities())
    excluded = [loc for loc in all_localities() if not in_study_area(loc)]
    denied = [loc for loc in excluded if loc.name in DISTRICT_DENY]
    audit = _deny_list_audit()
    return {
        "study_area_districts": list(STUDY_AREA_DISTRICTS),
        "out_of_district_excluded": {
            loc.name: DISTRICT_DENY[loc.name] for loc in denied
        },
        "out_of_district_excluded_count": len(denied),
        # A name in here that is not in the extract means the deny list has a
        # typo and is silently doing nothing. Surfaced rather than fixed here:
        # whether it should be added is a data question, not a code one.
        "deny_list_not_in_extract": audit["not_in_extract"],
        "unresolved_border_localities": list(BORDER_CLUSTER),
        "unresolved_border_note": (
            "Localities on or near the western district boundary that the "
            "dataset cannot resolve — data/places.geojson carries no admin_level "
            "tags and no boundary geometry. They are KEPT in the study area, "
            "because dropping a real delta village on a hunch is the worse "
            "error. Settle them against a boundary dataset."
        ),
        "study_area_max_lat": STUDY_AREA_MAX_LAT,
        "localities_in_study_area": in_area,
        "localities_excluded": len(excluded),
        "excluded_names": sorted(loc.name for loc in excluded),
        "excluded_reasons": {
            loc.name: _exclusion_reason(loc) for loc in sorted(
                excluded, key=lambda l: l.name
            )
        },
        "disclosure": (
            f"Scoped to {', '.join(STUDY_AREA_DISTRICTS)}, taken as everything "
            f"south of lat {STUDY_AREA_MAX_LAT} that is not explicitly denied "
            "above. The DEM bbox clips the northern Kolkata suburbs, whose "
            "density estimates would otherwise dominate the district totals, "
            "and the bbox also catches part of neighbouring Purba Medinipur, "
            "which is why some places are named and denied one by one. The "
            "latitude line is NOT the district boundary — it approximates it, "
            "and the real boundary is not in the dataset."
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
