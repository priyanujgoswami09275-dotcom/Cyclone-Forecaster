"""Time-stepped BFS flood propagation over the DEM grid.

Algorithm (CLAUDE.md "Reference code" — a BFS cellular automaton seeded from
the ocean, producing a sequence of flood frames rather than one static
polygon, as Architecture.md's /surge-zone contract requires):

1. The ocean cells (elevation <= 0) seed the frontier.
2. The water level rises linearly from 0 to the target surge over `n_steps`.
3. At each step, the frontier expands to 4-connected neighbours whose
   elevation is at or below the current water level.
4. Frames accumulate, so frame k is the flooded set at step k.

Three departures from the bare reference code, all load-bearing:

**Ocean connectivity is enforced.** The reference BFS seeds from the ocean and
only ever moves to low ground, so every flooded cell is ocean-connected by
construction. It is kept that way — an inland depression below the surge
level must NOT appear as a flooded lake, because surge cannot reach it. This
is the same guard the design spec calls for via `scipy.ndimage.label`; the BFS
frontier gets it for free.

**Nodata is excluded.** Cells that were nodata in the source raster are
treated as barriers, not terrain. Filling them with the minimum real elevation
(in `dem.py`) would make them floodable and would leak the flood into
unmeasured areas. `dem.py` fills them low for arithmetic safety, so this
module masks them back out at the end.

**Bangladesh is subtracted.** The DEM bbox reaches 89.2E — into Bangladesh —
and nothing about the terrain cares where the border is. The committed OSM
country polygon (`data/bangladesh_boundary.geojson`) is rasterised once onto
the DEM grid at cell centres (`bangladesh_cells`, cached) and subtracted
from every reported mask before any area is computed or any polygon built.
Subtraction rather than clipping to West Bengal, because the OSM-vs-DEM
coastline mismatch would delete real coastal WB cells under a clip, and the
flood is only ~0.5 km wide. The BFS still floods those cells — the physics
is untouched; only what the figures may count changes. See
`backend/data_pipeline/fetch_osm_boundary.py` for provenance.

The surge level is the model's output and is an ESTIMATE (see surge.py); the
frames are a scenario, not a forecast, and every API response must say so.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from rasterio import features

from .dem import Dem, load_dem
from .surge import SurgeResult, predict_surge

DEFAULT_STEPS = 10

# 4-connected (von Neumann) neighbourhood: the reference BFS in CLAUDE.md
# steps only up/down/left/right. `ndimage.maximum_filter` defaults to a full
# 3x3 square, which is 8-connected and lets water jump diagonally across a
# corner — that over-floods relative to the reference algorithm, so the
# cross-shaped footprint is load-bearing, not cosmetic.
FOUR_CONNECTED = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)

# --- Bangladesh exclusion (border honesty, 2026-10-17) ---------------------
#
# The DEM bbox reaches 89.2E — into Bangladesh — and until 2026-10-17 the
# flood extent ran over that foreign land unclipped: measured against a
# provisional lon 89.0 proxy, 16.7-22.2% of every "flooded land" figure was
# Bangladeshi territory (see CLAUDE.md's gotchas for the numbers). The fix
# is SUBTRACTION, not clipping to West Bengal: the OSM-vs-DEM coastline
# mismatch would delete real coastal WB cells under a clip-to-WB approach,
# and the flood is only ~0.5 km wide, so an over-precise clip is the more
# dangerous error. Subtraction can only remove cells OSM says are
# Bangladesh — it cannot eat the WB coast.
#
# The BFS itself still floods those cells (unchanged — nothing about the
# physics claims water stops at a border); only the reported masks, areas
# and downstream polygon exclude them, so every figure the API serves
# counts the study country alone.
#
# This module stays network-free (Rules.md): the polygon is a committed
# artefact, data/bangladesh_boundary.geojson — fetched once by
# backend/data_pipeline/fetch_osm_boundary.py from the OSM country relation
# (admin_level=2, ISO3166-1=BD; boundary (c) OpenStreetMap contributors,
# ODbL) and read from disk here, never fetched.

BOUNDARY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "bangladesh_boundary.geojson"
)


@lru_cache(maxsize=4)
def _bangladesh_cells(
    grid_shape: tuple[int, int],
    transform_key: tuple[float, float, float, float, float, float],
) -> np.ndarray:
    """Rasterise the committed polygon onto one DEM grid, cached.

    Keyed on the grid alone — same shape and transform always yield the
    same cells, so re-rasterising would be pure waste. The affine's six
    coefficients form the hashable key (Affine objects are not relied on
    to be hashable; the coefficients are).
    """
    from rasterio.transform import Affine

    if not BOUNDARY_PATH.exists():
        raise FileNotFoundError(
            f"{BOUNDARY_PATH} not found. It is a committed input by design "
            "(Rules.md: no live fetches) — regenerate it with "
            "backend/data_pipeline/fetch_osm_boundary.py, never by hand. "
            "The engine refuses to run rather than silently counting "
            "Bangladeshi land as flooded again."
        )
    fc = json.loads(BOUNDARY_PATH.read_text())
    geometries = [
        shape(feature["geometry"])
        for feature in fc.get("features", [])
        if feature.get("geometry")
    ]
    if not geometries:
        raise ValueError("boundary file carries no geometry")
    geom = geometries[0] if len(geometries) == 1 else unary_union(geometries)
    # all_touched=False is the cell-CENTRE rule: a cell is Bangladesh only
    # if its centre falls inside the polygon. Cell edges straddling the
    # border are the polygon's business, not the rasteriser's guesswork.
    raster = features.rasterize(
        [(geom, 1)],
        out_shape=grid_shape,
        transform=Affine(*transform_key),
        fill=0,
        all_touched=False,
        dtype="uint8",
    )
    return raster.astype(bool)


def bangladesh_cells(dem: Dem) -> np.ndarray:
    """Boolean mask of `dem`'s cells whose centre lies inside Bangladesh.

    READ-ONLY by convention: the array is shared through the cache across
    every caller in the process, so an in-place mutation would corrupt all
    subsequent results. Callers use it in fresh boolean expressions and
    never assign into it.
    """
    return _bangladesh_cells(dem.shape, tuple(dem.transform)[:6])

# Client-payload tuning for the polygonised flood extent. Both are LOSSY and
# the loss is reported per frame as `dropped_detail_km2`; neither affects the
# area figures, which come from the raster cell counts.
#
# SRTM stores integer metres, so a realistic surge fragments into tens of
# thousands of sub-pixel pieces. These two constants are what make the extent
# renderable on a phone; they are presentation limits, not physics.
MIN_PART_KM2 = 0.5  # a water body below this is DEM speckle, not a flood feature
SIMPLIFY_TOL_DEG = 0.0015  # ~170 m, ~3x the DEM's own cell size


@dataclass(frozen=True)
class FloodFrame:
    """One timestep of the flood simulation."""

    step: int
    water_level_m: float
    area_km2: float  # total flooded area, sea included, Bangladesh excluded
    land_area_km2: float  # flooded land only (excludes ocean and Bangladesh)
    new_land_area_km2: float  # land inundated *at this step*
    geometry: dict  # GeoJSON polygon of the land newly inundated at this step
    dropped_detail_km2: float = 0.0  # area omitted from `geometry` for rendering

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "water_level_m": round(self.water_level_m, 3),
            "area_km2": round(self.area_km2, 2),
            "land_area_km2": round(self.land_area_km2, 2),
            "new_land_area_km2": round(self.new_land_area_km2, 2),
            "dropped_detail_km2": round(self.dropped_detail_km2, 3),
            "geometry": self.geometry,
        }


@dataclass(frozen=True)
class FloodResult:
    frames: list[FloodFrame]
    surge: SurgeResult
    dem_shape: tuple[int, int]

    @property
    def final_area_km2(self) -> float:
        return self.frames[-1].area_km2 if self.frames else 0.0

    def to_feature_collection(self) -> dict:
        """All frames as one GeoJSON FeatureCollection, one feature per step.

        Only the final (peak-surge) feature carries a polygon — see
        `run_flood_model` for why. Every feature carries the step's areas as
        properties, so the timeline is available numerically regardless.

        `final_land_area_km2` is the modelled extent; `drawn_area_km2` is what
        the geometry actually shows, which excludes fragments below
        MIN_PART_KM2. A client that draws `drawn_area_km2` and reports
        `final_land_area_km2` is being accurate, not overstating impact.
        """
        last = self.frames[-1] if self.frames else None
        return {
            "type": "FeatureCollection",
            "final_land_area_km2": round(last.land_area_km2, 2) if last else 0.0,
            "drawn_area_km2": round(
                (last.land_area_km2 - last.dropped_detail_km2) if last else 0.0, 2
            ),
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "step": f.step,
                        "water_level_m": round(f.water_level_m, 3),
                        "area_km2": round(f.area_km2, 2),
                        "land_area_km2": round(f.land_area_km2, 2),
                        "new_land_area_km2": round(f.new_land_area_km2, 2),
                        "dropped_detail_km2": round(f.dropped_detail_km2, 3),
                    },
                    "geometry": f.geometry,
                }
                for f in self.frames
            ],
        }


def simulate_flood_propagation(
    elevation: np.ndarray,
    ocean_mask: np.ndarray,
    target_surge: float,
    n_steps: int = DEFAULT_STEPS,
) -> list[np.ndarray]:
    """BFS cellular automaton. Returns one boolean flood mask per timestep.

    Pure-array core, deliberately free of geo-math so it can be unit-tested
    against synthetic DEMs with known answers. `run_flood_model` wraps this
    with the real DEM, nodata masking, and polygonisation.
    """
    if target_surge <= 0:
        return [np.zeros_like(ocean_mask, dtype=bool) for _ in range(n_steps)]

    rows, cols = elevation.shape
    flooded = ocean_mask.copy()
    frames: list[np.ndarray] = []

    # The reference algorithm is a BFS: at each step, test the 4-neighbours of
    # every currently-flooded cell and flood those at or below the water level.
    # Done literally in Python that is ~3.7M cells x 10 steps on the real DEM
    # (>100 s), which cannot back an interactive slider.
    #
    # The step below is the same algorithm with the inner neighbour loop
    # expressed as a binary dilation: `maximum_filter` over a 3x3 structuring
    # element gives every cell adjacent to a flooded one, so one vectorised
    # pass replaces the Python loop. Correctness is identical — a cell is
    # flooded iff it is 4-connected to existing water and low enough, and the
    # frontier is re-derived from the full flooded set each step, which is what
    # keeps ocean connectivity enforced (an inland depression disconnected from
    # the sea is never reached).
    for step in range(1, n_steps + 1):
        water_level = target_surge * (step / n_steps)
        adjacent = ndimage.maximum_filter(
            flooded, footprint=FOUR_CONNECTED, mode="constant", cval=0
        )
        newly = adjacent & ~flooded & (elevation <= water_level)
        flooded |= newly
        frames.append(flooded.copy())
    return frames


def _mask_to_geometry(
    mask: np.ndarray,
    transform,
    cell_km2: float,
    min_part_km2: float = MIN_PART_KM2,
    simplify_deg: float = SIMPLIFY_TOL_DEG,
) -> tuple[dict, float]:
    """Vectorise a boolean mask to GeoJSON. Returns (geometry, dropped_km2).

    Why this is not a one-line `features.shapes(mask)`:

    SRTM quantises elevation to whole metres, so the entire 0-4 m delta —
      where all storm-surge flooding happens — is a plateau of identical
      integers with no sub-metre information. The flood boundary therefore
      lands on an arbitrary pixel boundary, and a realistic 3.6 m scenario
      comes out as ~45,000 disjoint fragments of ~2,800 m2 each. Polygonising
      the raw mask takes ~14 s and yields a ~22 MB payload, which cannot
      cross a phone connection, let alone back a slider.

    Two lossy steps keep it usable, and both are reported rather than hidden:

    1. Fragments below `min_part_km2` are dropped. On a 50 m grid this is sub-
       pixel-scale noise a map cannot show.
    2. Surviving parts are simplified to `simplify_deg` (~0.1 km, well below
      the DEM's own resolution) individually, which is far cheaper than a
      global `unary_union` (1.3 s vs 17 s) — and safe, because the parts are
      disjoint by construction.

    The caller must report `dropped_km2` so the API never claims the drawn
    polygon is the full modelled area.
    """
    if not mask.any():
        return {"type": "GeometryCollection", "geometries": []}, 0.0

    total_km2 = float(mask.sum()) * cell_km2
    min_cells = min_part_km2 / cell_km2

    # Drop negligible fragments at the raster level (cheap) before the far
    # more expensive vectorisation.
    labels, n = ndimage.label(mask, structure=FOUR_CONNECTED)
    if n == 0:
        return {"type": "GeometryCollection", "geometries": []}, total_km2
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0  # background
    keep_ids = np.flatnonzero(sizes >= min_cells)
    dropped_km2 = total_km2 - float(sizes[keep_ids].sum()) * cell_km2

    if keep_ids.size == 0:
        return {"type": "GeometryCollection", "geometries": []}, total_km2
    keep_mask = np.isin(labels, keep_ids)

    parts = [
        shape(s)
        for s, v in features.shapes(
            keep_mask.astype("uint8"), mask=keep_mask, transform=transform, connectivity=4
        )
        if v == 1
    ]
    if not parts:
        return {"type": "GeometryCollection", "geometries": []}, total_km2

    # `connectivity=4` on a diagonal-touching mask yields parts that share
    # corners, so a raw MultiPolygon of them is self-intersecting and invalid
    # GeoJSON. Unioning first both merges those into real water bodies and
    # guarantees validity. On the filtered mask this costs ~2 s; it is the
    # price of emitting geometry a client can actually render.
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    merged = unary_union(parts)
    merged = merged.simplify(simplify_deg, preserve_topology=True)
    if merged.is_empty:
        return {"type": "GeometryCollection", "geometries": []}, total_km2
    if isinstance(merged, MultiPolygon) or merged.geom_type == "Polygon":
        return mapping(merged), dropped_km2
    return mapping(merged), dropped_km2


def run_flood_model(
    wind_kmph: float,
    dem: Dem | None = None,
    n_steps: int = DEFAULT_STEPS,
) -> FloodResult:
    """Full flood simulation for a storm intensity. The Module B entry point.

    Predicts surge from wind via the trained regression, then propagates the
    flood across the real DEM in `n_steps` timesteps.

    The expensive polygon is built ONCE, from the cumulative final extent, and
    attached to the last frame. Per-step geometries are not emitted: the ten
    frames are thin nested fringes of the same water body, each carrying its
    own full coastline, which multiplies the payload by ~10x for no extra
    information a map can use. The per-step areas are still computed and
    exposed, so the timeline is real data — only the drawing is cumulative.
    """
    dem = dem or load_dem()
    surge = predict_surge(wind_kmph)

    cell_km2 = dem.cell_area_km2()
    ocean = dem.ocean_mask()
    # The border mask, not the BFS, decides what the figures may count.
    bd_cells = bangladesh_cells(dem)
    frames = simulate_flood_propagation(
        elevation=dem.elevation,
        ocean_mask=ocean,
        target_surge=surge.surge_m,
        n_steps=n_steps,
    )

    result_frames: list[FloodFrame] = []
    previous = np.zeros_like(ocean, dtype=bool)
    for i, mask in enumerate(frames, start=1):
        # Nodata is filled low in dem.py; exclude it so the flood extent only
        # covers genuinely-measured terrain. Bangladesh cells are excluded the
        # same way and for the same reason: the water may physically be there,
        # but no reported figure may count territory outside the study
        # country.
        effective = mask & (~dem.nodata_mask) & (~bd_cells)
        delta = effective & ~ocean & ~previous
        result_frames.append(
            FloodFrame(
                step=i,
                water_level_m=surge.surge_m * (i / n_steps),
                area_km2=float(effective.sum()) * cell_km2,
                land_area_km2=float((effective & ~ocean).sum()) * cell_km2,
                new_land_area_km2=float(delta.sum()) * cell_km2,
                geometry={"type": "GeometryCollection", "geometries": []},
            )
        )
        previous = effective

    # Cumulative land inundation at peak surge — the single drawable extent.
    # Bangladesh is subtracted BEFORE polygonising, so the geometry a client
    # draws can never disagree with the areas it is shown.
    final_land = frames[-1] & (~dem.nodata_mask) & ~ocean & (~bd_cells)
    geometry, dropped_km2 = _mask_to_geometry(final_land, dem.transform, cell_km2)
    result_frames[-1] = replace(
        result_frames[-1], geometry=geometry, dropped_detail_km2=dropped_km2
    )

    return FloodResult(frames=result_frames, surge=surge, dem_shape=dem.shape)
