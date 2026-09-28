"""FastAPI backend — the API contract in Architecture.md, nothing else.

    GET  /surge-zone?category={0-6}   flood polygon + areas
    GET  /exposure?category={0-6}     hospitals / substations / roads_cut_off
    GET  /routes?category={0-6}&origin={locality_id}   safe route + shelter
    GET  /allocation?category={0-6}   locality -> shelter assignment
    POST /advisory?category={0-6}&origin={locality_id}   DistrictAdvisory (Gemini)

Plus three small helpers the app needs to drive the slider: `/categories`
(the band table), `/localities` (the origin picker) and `/health`.

**Rules this layer is built to, not retrofitted with:**

- *No live network calls from a handler* (Rules.md). Every simulation byte
  comes from a committed file in `data/`. Overpass, IBTrACS and GEE are
  pre-fetch scripts in `backend/data_pipeline/` and are never imported here.
  `POST /advisory` is the single exception and it calls only Gemini — behind
  an explicit user action, never on slider movement, because the free tier
  would be exhausted in seconds (Rules.md).
- *Honesty metadata is not decoration.* Each response carries the caveats its
  own numbers require — the surge estimate's provenance, the modelled-vs-drawn
  area gap, the exposure definitions, the population method, the shelter
  dataset status. A client that drops a field is making a claim the backend
  refused to make, so the fields are not optional and not stripped for tidiness.
  The same rule extends to the AI layer: an advisory that fails
  `ai.advisory.validate_advisory` is withheld with a 502, not returned with a
  disclaimer bolted on.
- *Nothing is computed twice.* Flood propagation is ~6 s at high categories
  and there are only 7 categories, so results are cached by category for the
  life of the process. The slider is unusable without this. `/advisory` reuses
  those caches rather than re-deriving its inputs.

Run locally:
    venv/bin/uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import networkx as nx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from google.genai.errors import ServerError
from shapely.geometry import shape

#: Repo root, for the committed data files. `data/` holds the pre-fetched DEM,
#: OSM extracts and now the rendered overlays; all of it is committed by design
#: (Rules.md: no live fetches at request time).
REPO_ROOT = Path(__file__).resolve().parents[1]

# Load `.env` before anything reads the environment. `load_dotenv` does NOT
# overwrite a variable that is already set, so an exported GEMINI_API_KEY still
# wins over the file — useful in CI and on Render, where the key is injected
# into the environment rather than shipped in a file.
#
# `.env` is gitignored (see .gitignore) and `.env.example` is the committed,
# secret-free template. Rules.md: the key must never reach a tracked file.
load_dotenv()

from .ai.advisory import (
    ADVISORY_MODEL,
    DistrictAdvisory,
    EvacuationPriority,
    generate_advisory,
    plan_coverage,
    validate_advisory,
)
from .locations import (
    Locality,
    all_localities,
    building_count,
    buildings_near,
    get_locality,
    localities,
    population_methodology,
    scoping,
)
from .simulation.allocation import DemandNode, allocate_shelters
from .simulation.dem import load_dem
from .simulation.exposure import compute_exposure
from .simulation.flood import FloodResult, run_flood_model
from .simulation.routing import build_road_graph, safe_route
from .simulation.shelters import (
    Shelter,
    demo_shelters,
    load_shelters,
    shelter_dataset_status,
)
from .simulation.surge import (
    ANCHOR_IMD_BAND,
    ANCHOR_SURGE_M,
    ANCHOR_WIND_KMPH,
    IMD_BANDS,
    SURGE_LIMITATION,
    SURGE_METHOD,
    predict_surge,
)

# `IMD_BANDS` is weak-to-strong, which is the direction the slider runs
# (PRD: "Depression -> Super Cyclonic Storm"), so category index i is
# `IMD_BANDS[i]`. Every number below is derived from that one table in
# `surge.py` — nothing is restated here, so the API and the model cannot
# disagree about where a band starts.
#
# This module used to carry its own copy of the thresholds, and that copy held
# the **knots** column (17/28/34/48/64/90/120) while naming them `_KMPH` and
# presenting them to clients as km/h. See `surge.py` for the full account.
# The module-level `BAND_UPPER_KMPH = None` that used to sit here is gone too:
# the open-ended top band is now `IMD_BANDS[-1].upper_kmph is None`, so the
# fact that IMD documents no ceiling lives with the band it describes.

# The case study, exposed as a selectable preset so the app can show the real
# event rather than only band midpoints. Cyclone Remal, May 2024, landfall
# between Sagar Island and Khepupara: 110-120 kmph, surge ~1.0-1.5 m
# (CLAUDE.md). At 115 kmph the scaling law returns exactly 1.2 m.
REMAL_PRESET = {
    "id": "remal_observed",
    "label": "Remal as observed (May 2024)",
    "wind_kmph": 115.0,
    "surge_m": 1.2,
    "source": (
        "Cyclone Remal landfall between Sagar Island (West Bengal) and "
        "Khepupara (Bangladesh), May 2024. Documented landfall wind "
        "110-120 kmph gusting 135; surge ~1.0-1.5 m above astronomical tide. "
        "Wind and surge here are the midpoints of those two ranges."
    ),
}

# Total demo shelter capacity as a multiple of estimated exposed population.
# Above 1.0 so the transportation LP is feasible (its constraints require
# every person's demand to be met); low enough that individual shelters still
# fill up, which is what makes the capacity/distance trade-off real.
CAPACITY_HEADROOM = 1.10

# ADVISORY_MODEL is defined in ai/advisory.py, next to the call that uses it,
# so there is exactly one place the model string can live. Rules.md: pin it,
# don't silently swap versions. Re-exported here because every advisory
# response reports which model produced it.

app = FastAPI(
    title="Cyclone Impact & Infrastructure Vulnerability Forecaster",
    version="1.0.0",
    description=(
        "Case study: Cyclone Remal, May 2024 landfall between Sagar Island "
        "(West Bengal) and Khepupara (Bangladesh). Every figure is either an "
        "estimate or traceable to a named source; responses say which."
    ),
)

# The Expo client is a separate origin from the API. No auth exists (PRD:
# explicitly out of scope), so credentials are not an option either.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Gzip, because the flood polygon is the largest thing this service sends and
# it is almost entirely floating-point coordinates — text that compresses
# well. Measured before adding: GET /surge-zone?category=6 is 4,608,374 bytes
# uncompressed, which no phone should be asked to download on every slider
# step. `minimum_size=1000` keeps the many small JSON responses uncompressed,
# where the gzip header and CPU cost more than they save.
#
# Added AFTER CORSMiddleware deliberately: Starlette builds the middleware
# stack in reverse registration order, so the last one added is the outermost
# and therefore sees the response first. Gzipping outside CORS means the
# compressed body — not the raw one — is what the CORS headers are attached
# to, which is the ordering that actually works in practice.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Display overlays, rendered offline by `backend/tools/render_overlays.py`.
#
# Served as static files rather than computed per request because they are
# prebuilt images: the flood engine produces a ~1000 px PNG in one pass and
# re-running the BFS to serve a static asset would be absurd. Served at all
# because a mobile client cannot read a local file path on the developer's
# machine — it needs an HTTP URL to hand to `<Overlay>`.
#
# The mount is display-only. `/exposure`, `/routes` and `/allocation` do not
# read anything from here; they consume the flood engine's full-resolution
# results, unchanged. `overlays.json` says so too, in `disclosure`.
OVERLAY_DIR = REPO_ROOT / "data" / "overlays"
OVERLAY_URL_PREFIX = "/overlays"
if OVERLAY_DIR.is_dir():
    app.mount(
        "/overlays",
        StaticFiles(directory=str(OVERLAY_DIR)),
        name="overlays",
    )


class OverlayBoundsError(ValueError):
    """A prebuilt overlay's geographic bounds are not in the order `<Overlay>` needs."""


def assert_overlay_bounds(entry_id: str, bounds: dict) -> None:
    """Check one entry's bounds are ordered west < east and south < north.

    `react-native-maps` resolves `<Overlay bounds>` positionally —
    `bounds[0]` becomes the `northEast` corner and `bounds[1]` the
    `southWest` one (see `normalizeBounds` in its `src/MapOverlay.tsx`).
    It never inspects the numbers, so transposed or degenerate bounds do
    not raise there: the image is simply placed against a mirrored or
    zero-area box, and on a phone over the Bay of Bengal that reads as a
    plausible-looking map with the flood in the wrong place. Nothing
    upstream complains, so the check belongs at the boundary where the
    committed file is read.

    Equality fails as well as inversion. A collapsed box is not a valid
    placement either, and `>` rather than `>=` catches it for free.
    """
    try:
        west = float(bounds["west"])
        south = float(bounds["south"])
        east = float(bounds["east"])
        north = float(bounds["north"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OverlayBoundsError(
            f"overlay {entry_id!r}: bounds must carry numeric west/south/east/north, "
            f"got {bounds!r}"
        ) from exc

    for name, low, high, low_side, high_side in (
        ("longitude", west, east, "west", "east"),
        ("latitude", south, north, "south", "north"),
    ):
        if not high > low:
            raise OverlayBoundsError(
                f"overlay {entry_id!r}: {name} bounds are not ordered — "
                f"{low_side}={low}, {high_side}={high}, expected {low_side} < {high_side}. "
                "The image would be placed against a mirrored or empty bounding box."
            )


# --------------------------------------------------------------------------
# Category -> wind -> surge
# --------------------------------------------------------------------------


def category_band(index: int) -> tuple[str, float, float | None, float]:
    """(label, lower_kmph, upper_kmph, representative_kmph) for a category.

    Categories 0-5 take the **midpoint** of their km/h band, which IMD
    documents on both sides: 40, 55.5, 75, 103, 142 and 194 kmph.

    **Category 6 is open-ended.** IMD documents Super Cyclonic Storm as
    **>= 222 kmph with no upper bound**, so a band with one edge has no
    midpoint. It uses its documented lower threshold, `upper` is reported as
    `None`, and `wind_is_band_midpoint` is `False` so a client cannot mistake
    the threshold for a midpoint. (This module previously invented a 250 kmph
    ceiling and used its midpoint, 185 kmph, as though IMD published it.)

    Consequence, stated because it surprises: 222 kmph is the *weakest* wind
    in the top band, so the slider's maximum is not a worst case. The
    open-ended top of the scale is real and the API does not pretend
    otherwise.
    """
    band = IMD_BANDS[index]
    return (
        band.label,
        band.lower_kmph,
        band.upper_kmph,
        band.representative_kmph(),
    )


@lru_cache(maxsize=7)
def surge_for_category(index: int):
    return predict_surge(category_band(index)[3])


@lru_cache(maxsize=7)
def flood_for_category(index: int) -> FloodResult:
    """Flood extent for a category. Cached: ~6 s of raster work per call."""
    return run_flood_model(category_band(index)[3])


def _flood_shape(result: FloodResult):
    return shape(result.frames[-1].geometry)


def _category_header(index: int) -> dict:
    """The category context every endpoint's response opens with.

    Every response states the `method` that produced the surge number, the
    `anchor` it was scaled from, and a `limitation` string. A response that
    carried a bare `surge_m` let a client present a screening estimate as if
    it were a site-specific forecast; these three fields are what stop that.
    """
    label, lower, upper, wind = category_band(index)
    surge = surge_for_category(index)
    band = IMD_BANDS[index]
    return {
        "category": index,
        "imd_category": label,
        "band_kmph": {"lower": lower, "upper": upper},
        # IMD publishes both columns and bulletins quote knots far more often
        # than km/h. Shipping both makes it checkable that `band_kmph` is the
        # km/h column and not, as it briefly was, the knots one.
        "band_knots": {"lower": band.lower_knots, "upper": band.upper_knots},
        "wind_kmph": wind,
        # False only for the open-ended top band, where the representative is
        # the documented threshold rather than a midpoint of a documented band.
        "wind_is_band_midpoint": upper is not None,
        "surge_m": surge.surge_m,
        "method": surge.method,
        "anchor": {
            "wind_kmph": surge.anchor_wind_kmph,
            "surge_m": surge.anchor_surge_m,
            "event": REMAL_PRESET["label"],
            "source": REMAL_PRESET["source"],
        },
        "limitation": SURGE_LIMITATION,
        "surge": surge.to_dict(),
    }


# --------------------------------------------------------------------------
# Meta endpoints
# --------------------------------------------------------------------------


@app.get("/")
def root() -> dict:
    return {
        "service": "cyclone-impact-forecaster",
        "case_study": "Cyclone Remal, May 2024 (Sagar Island / Khepupara landfall)",
        "anchor_surge_m": 1.2,
        "anchor_source": "CLAUDE.md / PRD.md — forecast surge ~1.0-1.5 m, anchor 1.2 m",
        "endpoints": [
            "GET /categories",
            "GET /localities",
            "GET /surge-zone?category={0-6}",
            "GET /exposure?category={0-6}",
            "GET /routes?category={0-6}&origin={block_id}",
            "GET /allocation?category={0-6}",
            "POST /advisory?category={0-6}&origin={locality_id}  (Module D, Gemini)",
        ],
    }


@app.get("/health")
def health() -> dict:
    """Liveness plus the data the server actually has, for a demo pre-check."""
    try:
        dem_shape = load_dem().shape
        nodes = build_road_graph().number_of_nodes()
        places = len(localities())
        buildings = building_count()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "ok",
        "dem_shape": list(dem_shape),
        "road_graph_nodes": nodes,
        "localities": places,
        "localities_excluded_by_scoping": len(all_localities()) - places,
        "building_centroids": buildings,
        "shelters_verified": len(load_shelters()),
        "advisory_implemented": True,
        # Whether POST /advisory will actually work right now, without putting
        # the key's value anywhere near the response.
        "advisory_ready": bool(os.environ.get("GEMINI_API_KEY")),
        "advisory_model": ADVISORY_MODEL,
    }


@app.get("/categories")
def categories() -> dict:
    """The seven IMD bands the slider steps through, plus the case study.

    `presets` is the important addition. The seven bands are a classification
    scheme, not a set of events, and none of their midpoints is the storm this
    project is about — Remal made landfall at 110-120 kmph, which is Severe
    Cyclonic Storm (89-117 kmph) rather than any band's 103 kmph midpoint. So
    a client that can only step through categories 0-6 has no way to show the
    actual case study. The preset gives it one, by name and by id.
    """
    return {
        "source": (
            "India Meteorological Department cyclone wind classification "
            "(3-min mean sustained wind). IMD publishes each band in both "
            "knots and km/h; band_kmph is the km/h column and band_knots is "
            "the knots column, shipped so the two are checkable against each "
            "other."
        ),
        "representative_wind": (
            "band midpoint of the km/h band for categories 0-5; for category 6 "
            "(Super Cyclonic Storm) IMD documents no upper bound, so the "
            "representative is the documented 222 kmph threshold, "
            "band_kmph.upper is null and wind_is_band_midpoint is false"
        ),
        "method": SURGE_METHOD,
        "anchor": {
            "wind_kmph": ANCHOR_WIND_KMPH,
            "surge_m": ANCHOR_SURGE_M,
            "imd_band": ANCHOR_IMD_BAND,
        },
        "limitation": SURGE_LIMITATION,
        "categories": [
            {
                **_category_header(index),
                "note": (
                    "this band produces less surge than the DEM's 1 m vertical "
                    "resolution can represent, so the flood model returns no "
                    "inundation for it"
                    if 0 < surge_for_category(index).surge_m < 1.0
                    else ""
                ),
            }
            for index in range(7)
        ],
        "presets": [REMAL_PRESET],
    }


@app.get("/overlays")
def list_overlays() -> dict:
    """Prebuilt flood overlays: image URLs and the bounds to place them.

    A display index, not a computation. Each entry is a transparent PNG
    rendered offline by `backend/tools/render_overlays.py` from the flood
    engine's final mask, downsampled to ~1000 px and quantised into four
    depth classes. `image_url` is absolute because a mobile client cannot
    resolve a server-relative path into something `<Overlay>` will load.

    **Read the disclosure before using these for anything but drawing.** The
    raster is a picture of the modelled extent, not a queryable geometry
    layer: it cannot represent the sub-pixel fragments the polygon path drops,
    and two cells of different depth may share a colour. `/exposure`,
    `/routes` and `/allocation` are unaffected and still use the
    full-resolution results — this is a display layer, deliberately kept off
    the computation path.
    """
    index_path = OVERLAY_DIR / "overlays.json"
    if not index_path.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "Overlays have not been rendered. Run "
                "`venv/bin/python -m backend.tools.render_overlays` to build "
                "them from the committed DEM."
            ),
        )
    index = json.loads(index_path.read_text())
    overlays = []
    for entry in index["overlays"]:
        # Before this, not after: a bad entry is a bad placement, and the
        # endpoint is the only place the committed file is read, so it is
        # the only place a transposed box can still be caught. Fails as a
        # 500 rather than being silently passed through — a client that
        # cannot place the image is better served by an error than by a
        # flood drawn in the wrong place.
        try:
            assert_overlay_bounds(entry.get("id", "<unnamed>"), entry.get("bounds", {}))
        except OverlayBoundsError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        overlays.append(
            {
                **entry,
                "image_url": f"{OVERLAY_URL_PREFIX}/{entry['image']}",
            }
        )
    return {
        "generated_by": index.get("generated_by"),
        "target_width_px": index.get("target_width_px"),
        "surge_method": index.get("surge_method"),
        "anchor": index.get("anchor"),
        "limitation": index.get("limitation"),
        "is_display_raster": True,
        "disclosure": index.get("disclosure"),
        "count": len(overlays),
        "overlays": overlays,
    }


@app.get("/localities")
def list_localities() -> dict:
    """Locality ids for the route-origin picker, with their coordinates."""
    return {
        "count": len(localities()),
        "source": "OpenStreetMap place nodes (data/places.geojson, pre-fetched)",
        "note": (
            "radius_km is a search radius for the building-density estimate, "
            "not an administrative boundary"
        ),
        "scoping": scoping(),
        "localities": [locality.to_dict() for locality in localities()],
    }


# --------------------------------------------------------------------------
# The contract endpoints
# --------------------------------------------------------------------------


@app.get("/surge-zone")
def surge_zone(
    category: int = Query(..., ge=0, le=6, description="IMD category index 0-6"),
) -> dict:
    """Flood polygon at a category's representative intensity."""
    result = flood_for_category(category)
    payload = result.to_feature_collection()
    return {
        **_category_header(category),
        # `final_land_area_km2` is the modelled extent; `drawn_area_km2` is
        # what the returned polygon actually shows, after sub-0.5 km2 DEM
        # speckle is dropped for rendering. They differ at high surge, and
        # reporting the larger one as the flooded area would overstate it.
        "final_land_area_km2": payload["final_land_area_km2"],
        "drawn_area_km2": payload["drawn_area_km2"],
        "is_estimate": True,
        "area_disclosure": (
            "final_land_area_km2 is the MODELLED extent; drawn_area_km2 is what "
            "the polygon below actually renders. SRTM quantises elevation to "
            "whole metres, so the 0-4 m delta shatters into fragments and those "
            "below 0.5 km2 are dropped. Report the modelled figure, and say it "
            "is modelled."
        ),
        "frame_count": len(result.frames),
        "definitions": {
            "land_area_km2": "inundated land, excluding permanent ocean",
            "flood_polygon": "cumulative extent at peak surge; intermediate timesteps are areas only",
        },
        "geojson": payload,
    }


@app.get("/exposure")
def exposure(
    category: int = Query(..., ge=0, le=6, description="IMD category index 0-6"),
) -> dict:
    """Which hospitals, substations and roads the flood reaches."""
    flood = flood_for_category(category)
    result = compute_exposure(flood.frames[-1].geometry)
    payload = result.to_dict()
    return {
        **_category_header(category),
        "final_land_area_km2": payload_counts_land(flood),
        "hospitals": payload["hospitals"],
        "substations": payload["substations"],
        # Named `roads_cut_off` per Architecture.md; the definition below is
        # not connectivity, and travels with the number so a client cannot
        # quietly upgrade it to one.
        "roads_cut_off": payload["roads"],
        "definitions": payload["definitions"],
        "is_estimate": True,
    }


def payload_counts_land(flood: FloodResult) -> float:
    return round(flood.frames[-1].land_area_km2, 2)


@app.get("/routes")
def routes(
    category: int = Query(..., ge=0, le=6, description="IMD category index 0-6"),
    origin: str = Query(..., description="Locality id from /localities"),
) -> dict:
    """A flood-free route from a locality to its assigned shelter.

    Unreachable is a real answer here — a high surge severs the delta — so it
    returns 200 with `reachable: false` and a reason, not a 404.
    """
    locality = get_locality(origin)
    if locality is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown origin '{origin}'. Call GET /localities for valid ids."
            ),
        )

    flood = flood_for_category(category)
    allocation = allocation_for_category(category)
    shelter, assigned = _shelter_for(locality, allocation, shelters_for_category(category))

    route = safe_route(
        build_road_graph(),
        flood.frames[-1].geometry,
        (locality.lon, locality.lat),
        (shelter.lon, shelter.lat),
    )
    if not route.reachable:
        route = _diagnose_unreachable(route, locality, shelter, flood)
    return {
        **_category_header(category),
        "origin": locality.to_dict(),
        "shelter": shelter.to_dict(),
        "shelter_assignment_basis": assigned,
        "shelter_status": shelter_dataset_status(),
        "capacity_basis": _capacity_basis(category),
        **route.to_dict(),
        "definitions": {
            "route": (
                "Dijkstra shortest path over the committed OSM road network with "
                "every edge intersecting the flood extent removed; it is a "
                "distance in metres, not a travel time"
            ),
            "reachable": "false means no flood-free path exists — an answer, not an error",
        },
    }


@app.get("/allocation")
def allocation(
    category: int = Query(..., ge=0, le=6, description="IMD category index 0-6"),
) -> dict:
    """Capacity-aware shelter assignment per locality (transportation LP)."""
    flood = flood_for_category(category)
    populations = populations_for_category(category)
    # Same cached result /routes reads, so the two endpoints cannot drift
    # apart on which shelters exist or who is assigned where.
    result = allocation_for_category(category)
    return {
        **_category_header(category),
        "final_land_area_km2": payload_counts_land(flood),
        "localities_evaluated": len(populations),
        "allocation": result["assignment"],
        "shelter_loads": result["shelter_loads"],
        "unmet_demand": result["unmet_demand"],
        "total_person_km": result.get("total_person_km"),
        "message": result["message"],
        "shelter_status": result["status"],
        "capacity_basis": _capacity_basis(category),
        "population_method": population_methodology(),
        "is_estimate": True,
    }


@lru_cache(maxsize=1)
def _component_index() -> dict:
    """node -> connected-component id in the full (unflooded) road graph.

    Lets `/routes` tell "the flood severed this" apart from "these two places
    were never connected by the road data we have". Only the second is
    non-obvious from the outside, and reporting the first when the second is
    true would blame a storm for a dataset gap.
    """
    graph = build_road_graph()
    return {
        node: component_id
        for component_id, component in enumerate(nx.connected_components(graph))
        for node in component
    }


def _nearest_node(graph, point: tuple[float, float]):
    """Closest graph node to a coordinate, or None if the graph is empty."""
    if graph.number_of_nodes() == 0:
        return None
    return min(graph.nodes, key=lambda n: _haversine_km(point, n))


def _diagnose_unreachable(route, locality, shelter, flood):
    """Replace a misleading `reason` with the actual cause.

    `routing.safe_route` reports the same string whether water closed the
    route or the committed road extract simply has no path between the two
    points. The second is common here: `roads.geojson` holds arterials and
    `delta_roads.geojson` covers only the southern delta, so inland towns
    like Canning are not connected to the delta at all. Saying "cut off" at
    zero surge would read as a flood warning that does not exist.
    """
    graph = build_road_graph()
    origin_node = _nearest_node(graph, (locality.lon, locality.lat))
    shelter_node = _nearest_node(graph, (shelter.lon, shelter.lat))
    components = _component_index()

    flood_geom = _flood_shape(flood)
    has_flood = flood_geom is not None and not flood_geom.is_empty

    if origin_node is None or shelter_node is None:
        reason = "no road network is loaded"
    elif components.get(origin_node) != components.get(shelter_node):
        reason = (
            "no route: the committed OSM extract does not connect these two "
            "points. roads.geojson holds arterials and delta_roads.geojson "
            "covers the southern delta only, so many inland towns have no "
            "path to the delta. This is road-data coverage, not flooding"
            + ("" if has_flood else " (there is no flood at this category)")
        )
    elif not has_flood:
        reason = (
            "no route, and there is no flood at this category — the origin or "
            "shelter could not be matched to the road network"
        )
    else:
        # routing.py's own wording is already the right answer here: the two
        # points are connected in the dry network, so only water can explain it.
        reason = route.reason

    return replace(route, reason=reason)


def _capacity_basis(category: int) -> dict:
    """How the shelter capacities in this response were arrived at.

    The single most misreadable number in the API: a client that renders
    "Shelter C: 41,765 capacity" without this block is presenting a derived
    placeholder as a surveyed facility.
    """
    shelters = shelters_for_category(category)
    return {
        "shelters_are_real": any(not s.is_demo_data for s in shelters),
        "total_capacity_people": sum(s.capacity_people for s in shelters),
        "estimated_demand_people": sum(
            n.population for n in populations_for_category(category)
        ),
        "headroom_factor": CAPACITY_HEADROOM,
        "rule": (
            "Shelter locations and capacities are NOT surveyed. No verified "
            "shelter dataset exists for this district (data/shelters.json). "
            "When real shelters are loaded this block reports them unchanged; "
            f"until then, capacities are DERIVED as {CAPACITY_HEADROOM} x the "
            "estimated exposed population, distributed across placeholder "
            "locations in proportion to their nominal sizes. The scaling "
            "exists so the transportation LP is feasible and demonstrates the "
            "capacity/distance trade-off — it is not a claim about how many "
            "people any shelter holds. The only verified figure is that 15 "
            "Multipurpose Cyclone Shelters were built in South 24 Parganas "
            "(WBDMD, PMNRF scheme); their locations and capacities are not "
            "published."
        ),
    }


# --------------------------------------------------------------------------
# Gemini capacity handling
# --------------------------------------------------------------------------
#
# `gemini-3.8-flash` is intermittently capacity-blocked: 2026-09-28 saw
# `503 UNAVAILABLE` on a call that succeeded eight seconds later, and a
# read-only probe found `gemini-3.5-flash` failing 3/3 while 3.8 answered.
# That is a capacity blip, not a fault in this code and not a reason to swap
# models (Rules.md: pin the string, decide deliberately, never silently).
#
# So: retry the SAME model, briefly, and if it is still busy say so in the
# status code and the message. Retry never becomes a model swap (Rules.md pins
# the string) and never becomes an unbounded loop — three attempts, then stop
# and report how many were made, so a client can tell "we tried three times and
# the model stayed busy" from "we called once".
#
# Exhaustion is 503 with `Retry-After: 60`. 503 is the semantically correct
# code for "the model is busy, the identical request is likely to work shortly";
# 502 would say "this service failed to get a usable answer from upstream",
# which is exactly what the retries were there to prevent. Both stories stay in
# the message body, so a client reading only the status gets the actionable one
# and a human reading the body gets the detail.
#
# This has been 503 -> 502 -> 503 in one day. The middle state was a one-round
# change on request; it is now reverted. Recorded because a reader finding
# 502 and 503 both discussed in git history should know which is current and
# that the change was deliberate, not drift. Logged as §28 in MEMORY.md.

# Seconds to wait before attempt N+1. Three attempts leave two gaps, so only
# 2s and 4s fire; 8s is here so raising CAPACITY_MAX_ATTEMPTS needs no new
# numbers. The total actually waited is measured, not assumed, and reported.
CAPACITY_BACKOFF_SECONDS = (2, 4, 8)
CAPACITY_MAX_ATTEMPTS = 3
CAPACITY_RETRY_AFTER_SECONDS = 60

# Indirected so tests can stub the wait without patching the stdlib.
_sleep = time.sleep


class GeminiCapacityError(RuntimeError):
    """Every attempt returned 503 UNAVAILABLE. The model is at capacity."""


def _is_capacity_error(exc: ServerError) -> bool:
    """Is this a capacity block rather than a real server-side failure?

    Matched on both the numeric code and the status word because the SDK has
    used both in the wild, and a retry is only correct for the transient case.
    A 500 or a malformed-response error must not be retried — that one is ours
    to fix, and retrying it just spends quota.
    """
    if getattr(exc, "code", None) == 503:
        return True
    text = str(exc).upper()
    return "503" in text or "UNAVAILABLE" in text


def _generate_with_capacity_retry(*args, **kwargs) -> tuple[DistrictAdvisory, int]:
    """`generate_advisory`, retried across capacity blocks.

    Transparent: same positional and keyword arguments. Returns the advisory and
    the number of attempts it took, so the handler can report how hard it tried
    instead of a client guessing from a latency. The only error this raises
    itself is exhausted-retry exhaustion.

    Applies to both the first pass and the correction pass, and to nothing
    else: the correction pass is a different kind of retry (a drafting slip,
    not a busy model) and the two are kept separate so `attempts` in the
    response still means what it says.
    """
    last: ServerError | None = None
    waited = 0
    for attempt in range(1, CAPACITY_MAX_ATTEMPTS + 1):
        try:
            return generate_advisory(*args, **kwargs), attempt
        except ServerError as exc:
            if not _is_capacity_error(exc):
                raise
            last = exc
            if attempt < CAPACITY_MAX_ATTEMPTS:
                delay = CAPACITY_BACKOFF_SECONDS[attempt - 1]
                _sleep(delay)
                waited += delay
    raise GeminiCapacityError(
        f"{ADVISORY_MODEL} returned 503 UNAVAILABLE on all "
        f"{CAPACITY_MAX_ATTEMPTS} attempts over {waited}s. The model is at "
        f"capacity, not a fault in this service; the same request is worth "
        f"retrying in about {CAPACITY_RETRY_AFTER_SECONDS}s. Last error: {last}"
    ) from last


@app.post("/advisory")
def advisory(
    category: int = Query(..., ge=0, le=6, description="IMD category index 0-6"),
    origin: str = Query(..., description="Locality id from /localities"),
) -> dict:
    """Synthesise a district advisory from the simulation outputs (Module D).

    The only endpoint in this service that reaches the network, and only ever
    because a human pressed the "Generate Advisory" button (Rules.md: never
    call Gemini on slider `onChange`). Everything it sends to Gemini is the
    same payload the GET endpoints return, so the numbers in the prose are the
    numbers on the map.

    Upstream failures are reported honestly rather than papered over: a missing
    key is 503, a capacity-blocked model is retried three times on the same
    pinned model and then 503 with a Retry-After, any other Gemini failure is
    502, and output that fails `validate_advisory` even after one correction
    pass is 502 with the violations listed. A 200 always means the advisory
    passed every honesty check.
    """
    # Request validity is settled before server configuration, so a client with
    # a bad origin id hears about that even on an unconfigured server — the two
    # problems are independent and reporting only the second hides the first.
    locality = get_locality(origin)
    if locality is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown origin '{origin}'. Call GET /localities for valid ids.",
        )

    # Read at request time, not import time: the key must be in the process
    # environment of whoever is running this, and must never be written to a
    # tracked file (Rules.md). Testing also depends on this.
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail=(
                "The advisory service is not configured: GEMINI_API_KEY is not "
                "set in the server's environment. Set it there and restart — do "
                "not add it to a tracked file. The computed inputs are still "
                "available at /surge-zone, /exposure and /allocation."
            ),
        )

    # The endpoint functions, not the raw helpers: advisory.py is written
    # against the response shapes, and feeding it the same dicts the client
    # already has is what guarantees the prose and the map cannot disagree.
    surge_payload = surge_zone(category)
    exposure_payload = exposure(category)
    allocation_payload = allocation(category)

    facts = _origin_facts(category, locality)
    context = _origin_context(facts)

    try:
        result, gemini_calls = _generate_with_capacity_retry(
            surge_payload, exposure_payload, allocation_payload, context=context
        )
    except GeminiCapacityError as exc:
        # Caught before the blanket `except Exception` below, which would
        # otherwise report a busy model as a broken one.
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": str(CAPACITY_RETRY_AFTER_SECONDS)},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - any SDK failure is a 502 to the client
        raise HTTPException(
            status_code=502,
            detail=f"Gemini advisory generation failed: {type(exc).__name__}: {exc}",
        ) from exc

    # Before validating, not after: the origin entry is code-built, so
    # `validate_advisory` must not see the model's output as-is and reject the
    # requesting locality for not being in the allocation data.
    result = _ensure_origin_in_plan(result, facts, allocation_payload)

    violations = validate_advisory(result, allocation_payload, locality.name)
    attempts = 1
    if violations:
        # One retry, feeding the violations back as corrections. A schema-shaped
        # answer that broke an honesty rule is usually a drafting slip, and a
        # second pass with the specific failure named fixes it more often than
        # not. Two attempts is the ceiling: this is a user-facing button, and
        # the free tier is rate-limited (Rules.md).
        attempts = 2
        try:
            result, correction_calls = _generate_with_capacity_retry(
                surge_payload,
                exposure_payload,
                allocation_payload,
                context=context,
                corrections="\n".join(f"- {v}" for v in violations),
            )
            gemini_calls += correction_calls
        except GeminiCapacityError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"{exc} It failed its honesty checks first "
                    f"({'; '.join(violations)}), so the correction pass was also "
                    f"needed."
                ),
                headers={"Retry-After": str(CAPACITY_RETRY_AFTER_SECONDS)},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Gemini advisory retry failed: {type(exc).__name__}: {exc}. "
                    f"First attempt's violations: {'; '.join(violations)}"
                ),
            ) from exc
        # The retry replaced the whole object, so the code-built origin entry has
        # to go back in. Missed this once and the guarantee silently lapsed only
        # on the retry path, which is exactly when it is hardest to notice.
        result = _ensure_origin_in_plan(result, facts, allocation_payload)
        violations = validate_advisory(result, allocation_payload, locality.name)

    if violations:
        # Still wrong after the retry. Returning it would mean shipping prose
        # that invents a locality or quotes a real-sounding shelter occupancy
        # figure, so the caller gets the reason instead.
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "The generated advisory failed its honesty checks and was "
                    "withheld rather than returned. Re-run, or report this."
                ),
                "violations": violations,
            },
        )

    return {
        "advisory": result.model_dump(),
        "generated_for": {
            "category": category,
            "imd_category": surge_payload["imd_category"],
            "wind_kmph": surge_payload["wind_kmph"],
            "origin": locality.to_dict(),
            "origin_context": context,
            # The computed facts behind the origin's plan entry, so a client can
            # show "unreachable" without re-deriving it from prose.
            "origin_reachable": facts["reachable"],
            "origin_reason": facts["reason"],
            "origin_in_allocation": facts["in_allocation"],
            "origin_entry_added_in_code": not any(
                item.locality_name == locality.name
                for item in result.evacuation_plan[1:]
            ),
        },
        "model": ADVISORY_MODEL,
        "validated": True,
        "validation": {
            "checks": [
                "sms_dispatch_draft under 160 characters",
                "every evacuation_plan locality appears in the allocation data, "
                "or is the requesting origin",
                "every allocation locality appears in evacuation_plan",
                "demo shelter data is disclosed as provisional/placeholder",
            ],
            # "covered/total" over the ALLOCATION localities, which is what the
            # coverage check is about. The code-built origin entry is not one of
            # them (Sagar has no allocation row), so it does not inflate the
            # count; a client reading 12/12 knows the plan names all 12.
            "plan_coverage": plan_coverage(result, allocation_payload)["label"],
            # Two different counts, deliberately separate. `attempts` is
            # correction passes (1, or 2 when the first draft broke a check).
            # `gemini_calls` is HTTP calls actually made, including the ones
            # that came back 503 and were retried — the honest answer to "how
            # hard did you try", and the one that makes a slow response legible.
            "attempts": attempts,
            "gemini_calls": gemini_calls,
        },
    }


# --------------------------------------------------------------------------
# Per-category derived results (cached with their flood)
# --------------------------------------------------------------------------


@lru_cache(maxsize=7)
def populations_for_category(category: int) -> tuple[DemandNode, ...]:
    """Evacuation demand per locality at this category's flood extent.

    Each locality is passed only the buildings inside its own search radius
    (see locations.buildings_near) — the density count inside
    estimate_populations is unchanged by the pre-filter, but the work drops
    from ~50M point-in-box tests to a few million.
    """
    from .simulation.population import estimate_populations

    flood = flood_for_category(category)
    cell_km2 = load_dem().cell_area_km2()
    flood_geom = _flood_shape(flood)

    nodes: list[DemandNode] = []
    for locality in localities():
        estimates = estimate_populations(
            [(locality.name, locality.lon, locality.lat, locality.radius_km)],
            flood_geom,
            cell_km2,
            buildings_near(locality),
        )
        estimate = estimates[0]
        # None means too few mapped buildings to mean anything; that is a
        # genuine "no demand estimate", not a zero, so it is excluded from
        # the LP rather than silently contributing nothing.
        if estimate.population:
            nodes.append(
                DemandNode(
                    name=estimate.name,
                    lon=estimate.lon,
                    lat=estimate.lat,
                    population=estimate.population,
                )
            )
    return tuple(nodes)


@lru_cache(maxsize=7)
def shelters_for_category(category: int) -> tuple[Shelter, ...]:
    """The shelter set the LP allocates against at this category.

    Real shelters when `data/shelters.json` has any (it does not — see
    shelters.py for the provenance), otherwise the demo placeholders with
    capacities **derived from the estimated exposed population** rather than
    left at their arbitrary Module B values.

    Why derive them: the placeholder set totals 5,100 places against ~129,000
    estimated exposed people, so the LP is infeasible and returns no
    assignment at all — which hides the algorithm the PRD asks for. Scaling to
    CAPACITY_HEADROOM x demand makes the LP feasible while keeping individual
    shelter capacities binding, so it still has to trade distance against
    capacity across the district rather than trivially sending everyone to
    the nearest one.

    These capacities are still NOT real and stay `is_demo_data: true`. The
    derivation rule is stated in the response so no client can mistake a
    derived number for a surveyed one. When real shelters land, this returns
    them unchanged and the rule stops applying.
    """
    real = load_shelters()
    if real:
        return tuple(real)

    nodes = populations_for_category(category)
    total_demand = sum(node.population for node in nodes)
    base = demo_shelters()
    if total_demand <= 0:
        # No exposed population at this category; keep the base figures rather
        # than scaling a zero.
        return tuple(base)

    base_total = sum(s.capacity_people for s in base)
    target = math.ceil(total_demand * CAPACITY_HEADROOM)
    scaled = []
    allocated = 0
    for index, shelter in enumerate(base):
        if index == len(base) - 1:
            # Absorb the rounding remainder here so the total is >= target.
            capacity = target - allocated
        else:
            capacity = math.floor(target * shelter.capacity_people / base_total)
            allocated += capacity
        scaled.append(
            Shelter(
                name=shelter.name,
                lon=shelter.lon,
                lat=shelter.lat,
                capacity_people=capacity,
                is_demo_data=True,
            )
        )
    return tuple(scaled)


@lru_cache(maxsize=7)
def allocation_for_category(category: int) -> dict:
    return allocate_shelters(populations_for_category(category), list(shelters_for_category(category)))


def _origin_facts(category: int, locality: Locality) -> dict:
    """Code-derived facts about the requesting origin, used by `/advisory`.

    `/advisory` takes an origin so the advisory is written for where the person
    asking is standing, not for an anonymous district. This is the same routing
    result `/routes` would return, pulled from the cached helpers — no second
    graph search, and no second opinion about reachability.

    Structured rather than a pre-rendered string because the caller needs the
    same facts twice: as prompt text, and to build the origin's own
    `EvacuationPriority` entry when the model leaves it out (see
    `_ensure_origin_in_plan`).
    """
    flood = flood_for_category(category)
    allocation_result = allocation_for_category(category)
    shelter, basis = _shelter_for(
        locality, allocation_result, shelters_for_category(category)
    )
    route = safe_route(
        build_road_graph(),
        flood.frames[-1].geometry,
        (locality.lon, locality.lat),
        (shelter.lon, shelter.lat),
    )
    reason = route.reason
    if not route.reachable and reason and "no route:" not in reason:
        # Reuse _diagnose_unreachable's road-data-vs-flood distinction so the
        # advisory never tells someone to travel a road the extract simply
        # does not contain.
        reason = _diagnose_unreachable(route, locality, shelter, flood).reason
    return {
        "locality": locality,
        "shelter": shelter,
        "shelter_basis": basis,
        "reachable": route.reachable,
        "reason": reason,
        "length_km": route.to_dict().get("length_km"),
        "in_allocation": any(
            row["node"] == locality.name for row in allocation_result.get("assignment", [])
        ),
    }


def _origin_context(facts: dict) -> str:
    """`_origin_facts` rendered for the prompt."""
    locality = facts["locality"]
    if facts["reachable"]:
        reachability = (
            f"has a flood-free route to its assigned shelter "
            f"({facts['shelter'].name}), {facts['length_km']} km"
        )
    else:
        reachability = f"is UNREACHABLE at this intensity — {facts['reason']}"
    in_plan = (
        "It appears in the allocation locality list below."
        if facts["in_allocation"]
        else "It does NOT appear in the allocation locality list below, because "
        "no at-risk population estimate could be made for it. Say so plainly "
        "rather than inventing figures for it."
    )
    return (
        f"REQUESTING LOCALITY: {locality.name} ({locality.lon}, {locality.lat})\n"
        f"IT {reachability}.\n"
        f"{in_plan}\n"
        f"Write the advisory for this locality's residents, but keep every "
        f"figure district-wide and labelled as such."
    )


def _as_sentence(text: str) -> str:
    """`text` normalised into one clean sentence.

    `/routes`' `reason` is a clause with no terminal punctuation, and the
    code-built origin reasoning splices it into the middle of a sentence — so
    the two ran straight together and the entry read "...road-data coverage,
    not flooding Evacuation cannot proceed along the mapped road network".
    Whitespace is collapsed too, because the reason is built from several
    diagnostic fragments that each end in a space.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    return cleaned if cleaned[-1] in ".!?" else cleaned + "."


def _ensure_origin_in_plan(
    result: "DistrictAdvisory", facts: dict, allocation_payload: dict
) -> "DistrictAdvisory":
    """Guarantee the requesting origin appears in `evacuation_plan`.

    In the first live run, `origin=sagar` produced an advisory that never
    mentioned Sagar — not in the plan, not in the summary — even though the
    prompt context said "IT is UNREACHABLE at this intensity". Sagar has no
    population estimate, so it is not in the allocation locality list, and
    rule 3 of the system prompt told the model to use only those localities.
    The two rules collided and the locality lost.

    So the entry is built here, in code, from the same routing result the
    prompt was given. It leads the list: the person who pressed the button is
    the one reading it. Every word in `reasoning` traces to a computed fact —
    reachability, the shelter assignment, the population gap — and none of it
    is model-generated, so there is nothing here for the model to get wrong.
    """
    locality = facts["locality"]
    if any(item.locality_name == locality.name for item in result.evacuation_plan):
        return result

    if facts["reachable"]:
        priority = "HIGH"
        reasoning = (
            f"The requesting locality. A flood-free route of {facts['length_km']} km "
            f"to its assigned shelter ({facts['shelter'].name}) exists at this "
            f"intensity, so evacuation is feasible on the road network as mapped."
        )
    else:
        priority = "CRITICAL"
        reasoning = (
            f"The requesting locality. It is UNREACHABLE at this intensity: "
            f"{_as_sentence(facts['reason'])} Evacuation cannot proceed along the "
            f"mapped road network from here, so movement must be planned "
            f"off-network — this is the highest-priority locality in the "
            f"district at this intensity."
        )

    if not facts["in_allocation"]:
        reasoning += (
            " Note: no at-risk population estimate was available for this "
            "locality, so no population figure is quoted for it."
        )

    entry = EvacuationPriority(
        locality_name=locality.name, priority_level=priority, reasoning=reasoning
    )
    result.evacuation_plan.insert(0, entry)
    return result


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _shelter_for(
    locality: Locality, allocation_result: dict, shelters: tuple[Shelter, ...]
) -> tuple[Shelter, str]:
    """The shelter this locality is assigned to, or the nearest one.

    Prefers the LP's assignment — that is the capacity-aware answer, and it is
    what "assigned shelter" in the contract means. Falls back to nearest-by-
    distance when the locality has no estimated demand, so `/routes` still
    answers rather than 404-ing on a sparse locality.
    """
    by_name = {s.name: s for s in shelters}

    for row in allocation_result.get("assignment", []):
        if row["node"] == locality.name and row["assignments"]:
            chosen = by_name.get(row["assignments"][0]["shelter"])
            if chosen is not None:
                return chosen, "linear-programme assignment (capacity-aware)"

    origin = (locality.lon, locality.lat)
    nearest = min(shelters, key=lambda s: _haversine_km(origin, (s.lon, s.lat)))
    return nearest, "nearest shelter by distance (locality had no demand estimate)"
