"""Sanity checks on the committed Bangladesh boundary polygon.

The flood engine subtracts this polygon from its results (see
`backend/simulation/flood.py`), so a wrong polygon silently corrupts every
area, exposure and allocation figure the app serves. These tests pin the
properties the polygon must have for that subtraction to be trustworthy —
checked against names that are independently known to be on each side of the
border, not against the polygon's own output.

Why named anchors rather than a generic "looks like a border" test: the
polygon is data, and data correctness cannot be verified from itself. The
two DISTRICT_DENY places are in Satkhira District, Bangladesh (that is why
they are denied — see `backend/locations.py`); the served localities are in
the West Bengal study area by construction; the seven clinics are east of
89.0E inside Bangladesh; Barunhat Rural Hospital is the West Bengal facility
nearest the border that must NOT be swallowed by an over-eager polygon.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from backend.locations import all_localities, localities
from backend.simulation.dem import load_dem
from backend.simulation.flood import bangladesh_cells

REPO_ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = REPO_ROOT / "data" / "bangladesh_boundary.geojson"
HOSPITALS_PATH = REPO_ROOT / "data" / "hospitals.geojson"

# Named West Bengal delta blocks the polygon must not swallow. If one of
# these reads as inside Bangladesh, the subtraction is deleting real study
# area, which is the failure mode this whole design (subtraction, not
# clipping to West Bengal) exists to avoid.
WB_ANCHOR_LOCALITIES = ("Namkhana", "Gosaba", "Sagar", "Patharpratima", "Kakdwip")

# Clinics east of 89.0E that are in Satkhira District, Bangladesh. Matched by
# substring because OSM names carry spelling variants and suffixes.
BD_ANCHOR_HOSPITALS = (
    "Kaliganj",
    "Shyamnagar",
    "Friendship Hospital Shyamnagar",
    "Modan Clinic",
    "Jhorna Clinic",
    "Life care",
    "Unnayon Clinic",
)

# The deny-list ids (backend/locations.py DISTRICT_DENY) that are in the
# places extract. The polygon must contain both.
DENY_LIST_IDS = ("osm-7488876257", "osm-2481969300")


def _polygon_from(fc: dict):
    parts = [shape(f["geometry"]) for f in fc["features"]]
    assert parts, "boundary file carries no geometry"
    return parts[0] if len(parts) == 1 else unary_union(parts)


@pytest.fixture(scope="module")
def boundary() -> dict:
    fc = json.loads(BOUNDARY_PATH.read_text())
    assert fc.get("features"), "boundary file has no features"
    return fc


@pytest.fixture(scope="module")
def polygon(boundary) -> object:
    return _polygon_from(boundary)


def _hospital_features() -> list[tuple[str, object]]:
    fc = json.loads(HOSPITALS_PATH.read_text())
    return [
        ((f.get("properties") or {}).get("name") or "", shape(f["geometry"]))
        for f in fc["features"]
    ]


def _inside(polygon, lon: float, lat: float) -> bool:
    return bool(polygon.contains(Point(lon, lat)))


class TestFileShape:
    def test_exists_and_is_small(self):
        assert BOUNDARY_PATH.exists(), (
            "data/bangladesh_boundary.geojson is missing. Regenerate it with "
            "backend/data_pipeline/fetch_osm_boundary.py — it is a committed "
            "input by design (Rules.md: no live fetches), never hand-drawn."
        )
        size = BOUNDARY_PATH.stat().st_size
        assert size < 500_000, f"boundary file is {size:,} B; target was < 500 KB"

    def test_carries_osm_attribution(self, boundary):
        assert "OpenStreetMap" in json.dumps(boundary["features"][0].get("properties") or {}), (
            "the boundary must carry its ODbL/OpenStreetMap attribution "
            "in-file, so the provenance travels with the data"
        )

    def test_stays_inside_the_dem_bbox(self, polygon):
        w, s, e, n = load_dem().bounds
        pw, ps, pe, pn = polygon.bounds
        assert pw >= w - 0.01 and ps >= s - 0.01 and pe <= e + 0.01 and pn <= n + 0.01, (
            "the clipped boundary must stay (approximately) inside the DEM "
            "bbox — anything else is a fetch or clip bug"
        )


class TestNamedAnchors:
    def test_deny_list_places_are_inside(self, polygon):
        matched = []
        for loc in all_localities():
            if loc.id in DENY_LIST_IDS:
                matched.append(loc)
                assert _inside(polygon, loc.lon, loc.lat), (
                    f"{loc.name!r} ({loc.id}) is denied as Bangladeshi but "
                    "falls outside the boundary polygon"
                )
        assert len(matched) == len(DENY_LIST_IDS), (
            f"expected both deny-list ids in the places extract, got {len(matched)}"
        )

    def test_every_served_locality_is_outside(self, polygon):
        inside = [loc.name for loc in localities() if _inside(polygon, loc.lon, loc.lat)]
        assert inside == [], (
            f"served localities inside the Bangladesh polygon: {inside} — "
            "the subtraction would remove study-area demand nodes"
        )

    @pytest.mark.parametrize("name", WB_ANCHOR_LOCALITIES)
    def test_wb_anchor_localities_are_outside(self, name, polygon):
        matches = [loc for loc in localities() if name.lower() in loc.name.lower()]
        assert matches, f"no served locality matches {name!r} — the anchor list is stale"
        for loc in matches:
            assert not _inside(polygon, loc.lon, loc.lat), (
                f"{loc.name!r} is a West Bengal delta block but reads as "
                "inside Bangladesh"
            )

    @pytest.mark.parametrize("name", BD_ANCHOR_HOSPITALS)
    def test_bd_anchor_hospitals_are_inside(self, name, polygon):
        matches = [geom for hname, geom in _hospital_features() if name.lower() in hname.lower()]
        assert matches, f"no hospital matches {name!r} — the anchor list is stale"
        lon, lat = matches[0].centroid.x, matches[0].centroid.y
        assert _inside(polygon, lon, lat), (
            f"{name!r} at ({lon:.4f}, {lat:.4f}) is Bangladeshi but reads "
            "as outside the polygon"
        )

    def test_barunhat_rural_hospital_is_outside(self, polygon):
        matches = [
            (hname, geom)
            for hname, geom in _hospital_features()
            if "barunhat" in hname.lower()
        ]
        assert matches, "Barunhat Rural Hospital is not in the hospital extract"
        geom = matches[0][1]
        assert not _inside(polygon, geom.centroid.x, geom.centroid.y), (
            f"{matches[0][0]!r} at ({geom.centroid.x:.4f}, {geom.centroid.y:.4f}) "
            "is in West Bengal but reads as inside Bangladesh — the polygon is wrong"
        )


class TestRasterisedMask:
    """The engine's cell-level view of the same polygon."""

    def test_mask_is_non_empty_and_east_of_88_90(self):
        dem = load_dem()
        mask = bangladesh_cells(dem)
        assert mask.any(), "the rasterised Bangladesh mask is empty"
        # The westernmost column with any mask cell, by cell-centre longitude.
        # Hard floor 88.90E: west of that is unambiguously deep West Bengal
        # and a mask there means the polygon or its rasterisation is wrong.
        # The real border dips further east than one might guess — the
        # Ichamati river meander reaches lon 88.9384 near the northern bbox
        # edge (measured 2026-10-17; Taki Rural Hospital at 88.9283 and
        # Matrisadan Nursing Home at 88.9164 sit just west of the border and
        # are correctly outside) — so the mask legitimately contains cells
        # between 88.90 and 88.95. The named-anchor tests above are what
        # actually guard West Bengal; this floor only catches gross breakage.
        cols = mask.any(axis=0)
        first_col = int(cols.argmax())
        lon = dem.transform.c + dem.transform.a * (first_col + 0.5)
        assert lon >= 88.90, (
            f"the mask's westernmost cell centre is at lon {lon:.4f}, west of "
            "88.90E — that far west is unambiguously West Bengal, so the "
            "polygon (or its rasterisation) is wrong"
        )
