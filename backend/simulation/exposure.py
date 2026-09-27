"""Infrastructure exposure: which assets the flood reaches.

Pure `json` + `shapely` — no geopandas. Architecture.md lists tools rather
than mandating them, and geopandas is a heavy dependency this work does not
need (the design spec reaches the same conclusion).

| Layer                | Test                                    | Property    |
|----------------------|-----------------------------------------|-------------|
| Hospitals (560 pts)  | point inside the flood polygon          | submerged   |
| Substations (103)    | point inside the flood polygon          | submerged   |
| Roads (3712 ways)    | geometry intersects the flood polygon   | cut_off     |

**Honest limitation, stated here because the field name invites over-reading:**
`cut_off` means *the road's geometry intersects the flood extent*. It is NOT a
network connectivity analysis — a road can touch the flood and still be
traversable, and a bridge can stay open while everything around it is
underwater. True cut-detection needs the routing graph (see routing.py), which
is the Module B routing task rather than a point-in-polygon test. The API
response repeats this definition so a client cannot over-claim it.

All data loads from the committed GeoJSON in /data. Nothing here calls Overpass
or any network service (Rules.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


@dataclass(frozen=True)
class ExposureResult:
    hospitals: list[dict]
    substations: list[dict]
    roads: list[dict]

    def counts(self) -> dict[str, int]:
        return {
            "hospitals": len(self.hospitals),
            "substations": len(self.substations),
            "roads": len(self.roads),
        }

    def to_dict(self) -> dict:
        return {
            "hospitals": {"count": len(self.hospitals), "features": self.hospitals},
            "substations": {
                "count": len(self.substations),
                "features": self.substations,
            },
            "roads": {"count": len(self.roads), "features": self.roads},
            "definitions": {
                "hospital_submerged": "hospital point lies inside the flood polygon",
                "substation_submerged": "substation point lies inside the flood polygon",
                "road_cut_off": (
                    "road geometry intersects the flood polygon; this is NOT a "
                    "network connectivity analysis and does not imply the road "
                    "is impassable"
                ),
            },
        }


@lru_cache(maxsize=4)
def load_geojson(filename: str) -> dict:
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Pre-fetched and committed by design (Rules.md: "
            "no live fetches) — restore it from git or re-run "
            f"backend/data_pipeline/fetch_osm_infra.py."
        )
    return json.loads(path.read_text())


def _features(filename: str) -> list[dict]:
    return load_geojson(filename).get("features", [])


def _name(props: dict) -> str:
    """Best available human label for an OSM feature."""
    tags = props.get("tags", props) or {}
    for key in ("name", "name:en", "ref", "operator"):
        if tags.get(key):
            return str(tags[key])
    return ""


@lru_cache(maxsize=4)
def _point_features(filename: str) -> tuple[tuple[object, dict], ...]:
    """Parsed (geometry, properties) for point-like layers, cached per process.

    Not every hospital/substation in the OSM extract is a Point: the Overpass
    query returns mapped *areas*, and a small clinic or substation yard comes
    back as the LineString that traces its perimeter. In the committed data
    that is 100 of 560 hospitals and 101 of 103 substations — dropping them
    would lose more than a third of the infrastructure while looking correct.
    Both are therefore parsed as real geometries and tested with the same
    rule: submerged if the flood polygon covers them.
    """
    parsed = []
    for feature in _features(filename):
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        if kind not in ("Point", "LineString", "Polygon", "MultiPolygon"):
            continue
        try:
            geom = shape(geometry)
        except (ValueError, TypeError):
            continue
        if geom.is_empty:
            continue
        parsed.append((geom, feature.get("properties") or {}))
    return tuple(parsed)


@lru_cache(maxsize=4)
def _line_features(filename: str) -> tuple[tuple[object, dict], ...]:
    """Parsed (geometry, properties) for line layers, cached per process."""
    parsed = []
    for feature in _features(filename):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("LineString", "MultiLineString"):
            continue
        try:
            geom = shape(geometry)
        except (ValueError, TypeError):
            continue
        if geom.is_empty:
            continue
        parsed.append((geom, feature.get("properties") or {}))
    return tuple(parsed)


def _tags(props: dict) -> dict:
    return props.get("tags", props) or {}


def _record(geometry, props: dict, status: str) -> dict:
    """Compact GeoJSON feature carrying only what the app displays.

    Raw OSM properties are large and full of keys the mobile client never
    renders; shipping them all would multiply the payload for no benefit, so
    only identifying tags are kept.
    """
    tags = _tags(props)
    out = {"name": _name(props), "status": status}
    for key in ("amenity", "power", "highway", "operator", "ref", "emergency"):
        if tags.get(key):
            out[key] = str(tags[key])
    return {
        "type": "Feature",
        "properties": out,
        "geometry": geometry.__geo_interface__,
    }


def _flood_shape(flood):
    """Accept a GeoJSON geometry, Feature, or FeatureCollection.

    Returns None when there is no drawable flood extent, which callers treat
    as "nothing is exposed" rather than an error.
    """
    if not flood:
        return None
    kind = flood.get("type")
    if kind == "FeatureCollection":
        parts = [
            shape(f["geometry"])
            for f in flood.get("features", [])
            if f.get("geometry") and f["geometry"].get("type") != "GeometryCollection"
        ]
        parts = [p for p in parts if not p.is_empty]
        return unary_union(parts) if parts else None
    if kind == "Feature":
        geometry = flood.get("geometry") or {}
        if geometry.get("type") == "GeometryCollection":
            return None
        geom = shape(geometry)
        return None if geom.is_empty else geom
    if kind == "GeometryCollection":
        return None
    geom = shape(flood)
    return None if geom.is_empty else geom


def compute_exposure(flood) -> ExposureResult:
    """Which hospitals, substations, and roads the flood extent reaches.

    `flood` is normally the final frame's polygon from flood.run_flood_model.
    Only the cumulative peak-surge extent is tested; per-step deltas are not,
    because the app draws a single flood layer.
    """
    geom = _flood_shape(flood)
    if geom is None:
        return ExposureResult(hospitals=[], substations=[], roads=[])

    prepared = prep(geom)

    def submerged(candidate) -> bool:
        """Is this facility under water?

        Points use `contains` — a hospital marker must actually be inside the
        flood, not merely beside it. Area and perimeter features use
        `intersects`, because a building footprint is only "under water" if the
        flood reaches it at all; `contains` would reject the perimeter
        LineStrings that represent many of these facilities, since a closed
        ring cannot be strictly contained by a polygon that merely overlaps it.
        """
        if candidate.geom_type == "Point":
            return prepared.contains(candidate)
        return prepared.intersects(candidate)

    hospitals = [
        _record(facility, props, "submerged")
        for facility, props in _point_features("hospitals.geojson")
        if submerged(facility)
    ]
    substations = [
        _record(facility, props, "submerged")
        for facility, props in _point_features("substations.geojson")
        if submerged(facility)
    ]
    # A road counts as affected if any part of its geometry touches the flood.
    # `intersects` rather than `within` is deliberate: a road running through
    # the flood is affected even though most of it sits on dry land.
    roads = [
        _record(line, props, "cut_off")
        for line, props in _line_features("roads.geojson")
        if prepared.intersects(line)
    ]
    return ExposureResult(hospitals=hospitals, substations=substations, roads=roads)
