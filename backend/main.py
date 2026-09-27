"""FastAPI backend — the API contract in Architecture.md, nothing else.

    GET  /surge-zone?category={0-6}   flood polygon + areas
    GET  /exposure?category={0-6}     hospitals / substations / roads_cut_off
    GET  /routes?category={0-6}&origin={block_id}   safe route + assigned shelter
    GET  /allocation?category={0-6}   locality -> shelter assignment
    POST /advisory                    DistrictAdvisory (Module D — not yet built)

Plus three small helpers the app needs to drive the slider: `/categories`
(the band table), `/localities` (the origin picker) and `/health`.

**Rules this layer is built to, not retrofitted with:**

- *No live network calls from a handler* (Rules.md). Every byte comes from a
  committed file in `data/`. Overpass, IBTrACS and GEE are pre-fetch scripts
  in `backend/data_pipeline/` and are never imported here.
- *Honesty metadata is not decoration.* Each response carries the caveats its
  own numbers require — the surge estimate's provenance, the modelled-vs-drawn
  area gap, the exposure definitions, the population method, the shelter
  dataset status. A client that drops a field is making a claim the backend
  refused to make, so the fields are not optional and not stripped for tidiness.
- *Nothing is computed twice.* Flood propagation is ~6 s at high categories
  and there are only 7 categories, so results are cached by category for the
  life of the process. The slider is unusable without this.

Run locally:
    venv/bin/uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import math
from dataclasses import replace
from functools import lru_cache

import networkx as nx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from shapely.geometry import shape

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
from .simulation.surge import IMD_CATEGORIES, predict_surge

# `IMD_CATEGORIES` is ordered strong-to-worst for threshold lookups, but the
# slider runs weak-to-strong (PRD: "Depression -> Super Cyclonic Storm"), so
# category index i is the i-th entry counting from the END of that tuple.
# Bounds are the IMD 3-min mean wind classification table (see surge.py).
_CATEGORY_BANDS = tuple(reversed(IMD_CATEGORIES))  # Depression .. Super
_BAND_LOWER_KMPH = (17.0, 28.0, 34.0, 48.0, 64.0, 90.0, 120.0)
BAND_UPPER_KMPH = 250.0  # the open-ended top band's working ceiling

# Total demo shelter capacity as a multiple of estimated exposed population.
# Above 1.0 so the transportation LP is feasible (its constraints require
# every person's demand to be met); low enough that individual shelters still
# fill up, which is what makes the capacity/distance trade-off real.
CAPACITY_HEADROOM = 1.10

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


# --------------------------------------------------------------------------
# Category -> wind -> surge
# --------------------------------------------------------------------------


def category_band(index: int) -> tuple[str, float, float, float]:
    """(label, lower_kmph, upper_kmph, representative_kmph) for a category.

    The representative is the **midpoint** of the band, i.e. the typical
    intensity for that IMD category rather than its worst case. It is
    returned in every response, so a client can display or re-derive the
    exact wind rather than treating the category as the number.

    Consequence, stated here because it is surprising: the surge regression
    cannot resolve anything below ~115 kmph (see MEMORY.md "Flagged for
    review" #8 — n=4 training points, and the fitted line goes negative at
    low wind). Categories 0-4 therefore return 0 m and category 5 returns
    ~0.06 m. That is the model being honest, not the API failing to wire
    something up, and it is not worked around here.
    """
    threshold, label = _CATEGORY_BANDS[index]  # tuple order is (kmph, label)
    lower = _BAND_LOWER_KMPH[index]
    upper = _BAND_LOWER_KMPH[index + 1] if index < 6 else BAND_UPPER_KMPH
    return label, lower, upper, (threshold + upper) / 2 if index < 6 else (120.0 + BAND_UPPER_KMPH) / 2


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
    """The category context every endpoint's response opens with."""
    label, lower, upper, wind = category_band(index)
    surge = surge_for_category(index)
    return {
        "category": index,
        "imd_category": label,
        "band_kmph": {"lower": lower, "upper": upper},
        "wind_kmph": wind,
        "wind_is_band_midpoint": True,
        "surge_m": surge.surge_m,
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
            "POST /advisory (Module D — not implemented yet)",
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
        "advisory_implemented": False,
    }


@app.get("/categories")
def categories() -> dict:
    """The seven IMD bands the slider steps through."""
    return {
        "source": "India Meteorological Department cyclone wind classification (3-min mean)",
        "representative_wind": "band midpoint",
        "categories": [
            {
                **_category_header(index),
                "note": (
                    "the surge model cannot resolve below ~115 kmph, so this "
                    "band returns 0 m of surge (see surge.loo_mae_m)"
                    if surge_for_category(index).surge_m == 0
                    else ""
                ),
            }
            for index in range(7)
        ],
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


@app.post("/advisory")
def advisory() -> dict:
    """Not built yet — Module D (the Gemini advisory layer) is next.

    Declared now, returning 501 rather than a stub, so the mobile client can
    detect the gap instead of rendering an empty advisory. Nothing here calls
    Gemini: Rules.md forbids calling it outside an explicit user action, and
    the free tier would be exhausted by slider movement alone.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "The Gemini advisory layer (Module D) is not implemented yet. "
            "See /categories and /exposure for the computed inputs it will "
            "consume."
        ),
    )


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
