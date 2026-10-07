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
- *Nothing is computed twice.* Flood propagation is ~16 s at the top band, so
  the derived results — flood, populations, shelters, allocation — are cached
  for the life of the process. The slider is unusable without this, and
  `/advisory` reuses those caches rather than re-deriving its inputs.

  The key is `(cyclone_id, scenario_id)` via `ScenarioContext`, **not** the
  category. Both name a strength and only one can win, but they are not the
  same thing: `observed` is a storm's own wind, so a category-keyed cache hands
  the second storm the first one's numbers with nothing to indicate a mix-up.

Run locally:
    venv/bin/uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import networkx as nx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from google.genai.errors import ServerError
from pydantic import BaseModel, Field
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
    RiskAnalysis,
    generate_advisory,
    generate_risk_analysis,
    plan_coverage,
    validate_advisory,
)
from .cyclones.base import (
    CycloneRecord,
    LiveStatus,
    iso_time_to_rfc3339,
    live_unavailable_reason,
)
from .cyclones.live import AtcfLiveSource
from .cyclones.registry import CATALOGUE_LIMITATION, registry
from .cyclones.scenarios import (
    DEFAULT_CYCLONE_ID,
    ScenarioContext,
    resolve_scenario,
    scenarios_for,
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
from .ml.storm_peak_intensity import StormPeakEstimate, baseline_estimate
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
    imd_category,
    predict_surge,
    surge_for_wind,
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


def _context(
    category: int | None = None,
    cyclone_id: str | None = None,
    scenario_id: str | None = None,
) -> ScenarioContext:
    """Turn the optional query parameters into the pair everything is keyed on.

    **Precedence: `scenario_id` beats `category`.** Both name a strength and
    only one can win, so the more specific one does; when both are present the
    response says which was used, via `_provenance`.

    `category` is not ignored. It is what every existing client sends, and
    `?category=3` has to keep meaning category 3 — an earlier draft of this
    function dropped it, which made every category return the default scenario's
    flood extent. That is the kind of silent wrongness this module exists to
    prevent, and it is why `tests/test_module_c.py` caught it.

    `cyclone_id` alone falling back to Remal is deliberate and disclosed: an
    absent parameter means "the default cyclone", which is what every existing
    client sends. An id that is *present but unknown* is an error, never a
    silent substitution.
    """
    if cyclone_id is not None and registry().get(cyclone_id) is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown cyclone_id {cyclone_id!r}. The catalogue holds "
                f"{len(registry().historical())} North Indian Ocean storms; "
                f"call GET /cyclones for the list."
            ),
        )
    if scenario_id is not None:
        chosen = scenario_id
    elif category is not None:
        chosen = f"cat{category}"
    else:
        chosen = "cat6"

    ctx = ScenarioContext(
        cyclone_id=cyclone_id or DEFAULT_CYCLONE_ID,
        scenario_id=chosen,
    )
    try:
        ctx.resolve(registry().get(ctx.cyclone_id))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ctx


def _provenance(ctx: ScenarioContext) -> dict:
    """What every response says about what it computed and how.

    **Merged into the top level of every response**, not nested under a
    `provenance` key. A client that wants to know whether a number is a category
    default, a chosen scenario, or another storm's figure should not have to
    know which endpoints nest it — the whole point is that no response is
    ambiguous about itself.

    `generated_at` and the limitations are here rather than repeated per endpoint
    because a disclosure that has to be remembered is a disclosure that gets
    forgotten.

    **The scenario's limitation is `scenario_limitation`, not `limitation`.**
    `_category_header` already returns a `limitation` — the surge law's, which
    CLAUDE.md requires on every surge figure — and two disclosures sharing one
    key means one silently overwrites the other. That happened: merging this
    dict after the header replaced the surge disclosure with the scenario's, and
    `tests/test_module_c.py::test_top_level_caveats_travel_too` caught it. A
    key that can only hold one of two required strings is a bug waiting for the
    next endpoint to add a third.
    """
    scenario = ctx.resolve(registry().get(ctx.cyclone_id))
    return {
        "cyclone_id": ctx.cyclone_id,
        "scenario_id": ctx.scenario_id,
        "wind_kmph": scenario.wind_kmph,
        "imd_category": scenario.imd_category,
        "wind_is_band_midpoint": scenario.wind_is_band_midpoint,
        "scenario_kind": scenario.kind,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scenario_limitation": scenario.limitation,
        # Kept, and named for what it is: the scenario's own disclosure. Endpoints
        # that carry a surge figure merge this first and their own header second,
        # so the surge limitation wins the `limitation` key without either being
        # lost.
        "limitation": scenario.limitation,
    }


def _cyclone_block(record: CycloneRecord | None) -> dict | None:
    """The storm a response is about, or `None` when it is not about one.

    Present on every response that is scoped to a cyclone, so a client never has
    to infer from a bare id which storm's figures these are.
    """
    if record is None:
        return None
    return {
        "cyclone_id": record.cyclone_id,
        "name": record.name,
        "season": record.season,
        "basin": record.basin,
        "subbasin": record.subbasin,
        "peak_wind_kmph": record.peak_wind_kmph,
        "waypoint_count": len(record.waypoints),
    }


def _envelope(ctx: ScenarioContext, **body) -> dict:
    """A response body with its provenance merged in.

    One place, so no endpoint can ship a number without saying which
    `(cyclone_id, scenario_id)` pair produced it.
    """
    return {**_provenance(ctx), **body}


@lru_cache(maxsize=64)
def surge_for_scenario(ctx: ScenarioContext):
    """The deterministic surge figure for one (cyclone, scenario) pair.

    Keyed on the **pair**, not the category. Two cyclones at the same strength
    resolve to the same wind, so a category-keyed cache hands the second one the
    first one's numbers — a wrong answer with nothing to indicate a mix-up. The
    ML storm-peak estimate is deliberately absent: it is a second, separately
    labelled figure and must never reach this calculation.
    """
    return predict_surge(ctx.wind_kmph(registry().get(ctx.cyclone_id)))


@lru_cache(maxsize=64)
def flood_for_scenario(ctx: ScenarioContext) -> FloodResult:
    """Flood extent for one (cyclone, scenario) pair. ~6 s of raster work."""
    return run_flood_model(ctx.wind_kmph(registry().get(ctx.cyclone_id)))


def _flood_shape(result: FloodResult):
    return shape(result.frames[-1].geometry)


def _category_header(
    index: int,
    ctx: ScenarioContext,
    *,
    wind_kmph: float | None = None,
    band_index: int | None = None,
    is_band_midpoint: bool | None = None,
) -> dict:
    """The category context every endpoint's response opens with.

    Every response states the `method` that produced the surge number, the
    `anchor` it was scaled from, and a `limitation` string. A response that
    carried a bare `surge_m` let a client present a screening estimate as if
    it were a site-specific forecast; these three fields are what stop that.

    Takes the context as well as the index, because the band alone no longer
    identifies the answer: the same band under a different cyclone is a
    different scenario, and the response has to say which it computed.

    **Two keyword arguments, because two callers need different things.**

    `wind_kmph` is the wind that produced *this* response's surge. `/categories`
    calls this once per band and passes that band's own wind, so the seven rows
    stay seven different numbers; `/surge-zone` and `/exposure` pass the
    resolved scenario's wind, because that is what their surge was computed
    from. Both directions fail silently: a category list that repeats one number
    seven times, or a surge figure labelled with a wind that did not produce it.

    `band_index` is which band's bounds to report. It defaults to `index`, and
    is overridden when the resolved scenario *is* a band — so
    `?category=2&scenario_id=cat4` reports cat4's bounds rather than cat2's
    next to cat4's wind.
    """
    if wind_kmph is None:
        wind_kmph = category_band(index)[3]
    if band_index is None:
        band_index = index
        scenario = ctx.resolve(registry().get(ctx.cyclone_id))
        if scenario.kind == "observed":
            # A non-band scenario names a real storm's wind. The reported label
            # and bounds must describe the band that wind actually falls in, not
            # the separately chosen category index — otherwise the header can
            # call a 120.4 kmph wind "Severe" (the requested cat3 band) while
            # surge.imd_category correctly calls it Very Severe.
            matches = [
                i for i, b in enumerate(IMD_BANDS) if b.label == imd_category(wind_kmph)
            ]
        else:
            # A band scenario names its own band; report *that* one, even when
            # the requested index points elsewhere (e.g. ?category=3&scenario_id=cat6).
            matches = [
                i for i, b in enumerate(IMD_BANDS) if b.label == scenario.imd_category
            ]
        if matches:
            band_index = matches[0]

    label, lower, upper, _band_wind = category_band(band_index)
    surge = predict_surge(wind_kmph)
    band = IMD_BANDS[band_index]
    if is_band_midpoint is None:
        # The default is the band's own answer: a band with an upper bound has a
        # midpoint, the open-ended top band has a threshold instead.
        is_band_midpoint = upper is not None
    return {
        "category": index,
        "imd_category": label,
        "band_kmph": {"lower": lower, "upper": upper},
        # IMD publishes both columns and bulletins quote knots far more often
        # than km/h. Shipping both makes it checkable that `band_kmph` is the
        # km/h column and not, as it briefly was, the knots one.
        "band_knots": {"lower": band.lower_knots, "upper": band.upper_knots},
        "wind_kmph": wind_kmph,
        # False for the open-ended top band, where the representative is the
        # documented threshold rather than a midpoint of a documented band — and
        # for an `observed` scenario, which is a real storm's wind and not a
        # band's at all.
        "wind_is_band_midpoint": is_band_midpoint,
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
    """The service index: what this is, and every route it serves."""
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
            "GET /track",
            "GET /routes?category={0-6}&origin={block_id}",
            "GET /allocation?category={0-6}",
            "POST /advisory?category={0-6}&origin={locality_id}  (Module D, Gemini)",
            "GET /cyclones",
            "GET /cyclones/{id}/track",
            "GET /scenarios?cyclone_id={id}",
            "GET /live-cyclone",
            "GET /comparison?cyclone_ids={a},{b}",
            "POST /risk-analyst",
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
def categories(
    cyclone_id: Annotated[
        str | None, Query(description="IBTrACS SID. Absent means the default case study.")
    ] = None,
    scenario_id: Annotated[
        str | None, Query(description="`cat0`-`cat6`, or `observed`.")
    ] = None,
) -> dict:
    """The seven IMD bands the slider steps through, plus the case study.

    `presets` is the important addition. The seven bands are a classification
    scheme, not a set of events, and none of their midpoints is the storm this
    project is about — Remal made landfall at 110-120 kmph, which is Severe
    Cyclonic Storm (89-117 kmph) rather than any band's 103 kmph midpoint. So
    a client that can only step through categories 0-6 has no way to show the
    actual case study. The preset gives it one, by name and by id.
    """
    # This endpoint reports every band, so it has no single category to honour;
    # the scenario here only names what the *default* view is.
    ctx = _context(None, cyclone_id, scenario_id)
    return {
        # Provenance like every other response. `/categories` is a classification
        # scheme rather than one scenario's result, so it has no surge figure of
        # its own to disclose — but it is still computed for a cyclone and a
        # scenario, and a client should not have to know which endpoints hide
        # their provenance one level down.
        **_provenance(ctx),
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
                # Each band's OWN surge, not the requested scenario's. This row
                # describes the band, so reading it from `ctx` would repeat the
                # same number seven times and silently empty the dead-zone note
                # that categories 0-3 exist to carry. Passed explicitly so the
                # intent is in the call rather than in a default.
                **_category_header(
                    index, ctx,
                    wind_kmph=category_band(index)[3],
                    # Each row is its own band even when the request names a
                    # (different) scenario — otherwise the scenario's label
                    # would relabel all seven rows.
                    band_index=index,
                ),
                "note": (
                    "this band produces less surge than the DEM's 1 m vertical "
                    "resolution can represent, so the flood model returns no "
                    "inundation for it"
                    if 0 < surge_for_wind(category_band(index)[3]) < 1.0
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
    depth classes.

    `image_url` is **server-relative** — `/overlays/flood_cat6.png` — and the
    client prepends the API base. `mobile/api.ts:overlayImageUrl` does this for
    the native build and `WebImpactMap.tsx` does the same concatenation, because
    a bare relative path resolves against the Metro bundler's own origin and
    404s. This docstring previously claimed the field was absolute; it was not,
    and a client written to that description would double-prefix the base URL
    into `https://api.example.com/https://api.example.com/overlays/…`.

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


#: Committed IBTrACS extract for the case-study cyclone. Read-only, never
#: fetched at request time (Rules.md) — the whole file is 6 KB, so there is
#: nothing to gain from streaming it and every reason to check it instead.
TRACK_PATH = REPO_ROOT / "data" / "remal_track.geojson"

#: 1 knot in km/h. The source column is USA_WIND in knots; the rest of this
#: service reasons in km/h because that is the unit IMD bands are published
#: in, so the conversion happens once here rather than in every client.
KNOTS_TO_KMPH = 1.852


class TrackDataError(ValueError):
    """The committed track file is missing, malformed, or not what it claims."""


def _iso_z(text: str) -> str:
    """`2024-05-23 12:00:00` -> `2024-05-23T12:00:00Z`, or raise.

    Delegates to `cyclones.base.iso_time_to_rfc3339`, which is the only place
    the wire spelling is decided. `/track` reads a file and `track_payload`
    reads the catalogue, whose `iso_time` is IBTrACS's own space-separated
    spelling — and `/live-cyclone` converts with the same function — so no two
    endpoints can come apart. This wrapper exists only to raise this module's
    error type, which `tests/test_track.py` names.
    """
    try:
        return iso_time_to_rfc3339(text)
    except ValueError as exc:
        raise TrackDataError(str(exc)) from exc


def _parse_track_timestamp(raw: object, waypoint_index: int) -> str:
    """`iso_time` -> RFC 3339, or raise.

    The same conversion as `_iso_z`, with the waypoint's position in the file
    attached: a bare "invalid timestamp" sends you hunting through forty fixes
    for the one that broke. IBTrACS writes `2024-05-25 12:00:00` — no timezone
    marker, no offset. It is UTC, and the response says so in a `timezone` field
    rather than silently attaching one, so a client that needs an offset gets a
    truthful field instead of a guess baked into a string.

    Both halves of the message are asserted by `test_a_bad_timestamp_names_the_waypoint`.
    """
    try:
        return iso_time_to_rfc3339(raw)
    except ValueError as exc:
        raise TrackDataError(f"waypoint {waypoint_index}: {exc}") from exc


def load_track() -> dict:
    """Parse `data/remal_track.geojson` into the shape `/track` sends.

    Returns the cyclone's identity, the ordered polyline, and one entry per
    fix. Split out from the endpoint so the parsing is testable against
    fixtures that are not the committed file.

    **Why this is an endpoint and not a static mount like `/overlays`.** The
    overlay layer is a mount because it is a picture: bytes in, bytes out,
    with a bounds box beside it that needed a separate index file anyway.
    This one has real work to do that a file server cannot do at all:

      1. **Missing wind must not read as calm.** The fetch script coerces a
         blank USA_WIND to `0.0`, so five of the nineteen fixes in the
         committed file carry `usa_wind_kt: 0.0` that mean *not reported*,
         not 0 knots. Serving the file raw would let a client draw a
         historical cyclone that appears to stop dead in the middle of the
         Bay and then restart. A static mount cannot distinguish the two
         cases; here it is a `wind_kt: null` plus `wind_reported: false`.
      2. **Units and the timezone need stating once, server-side.** The
         source is knots with unzoned timestamps; every client would
         otherwise repeat the same conversion, and get it wrong
         independently.
      3. **A malformed file should be a clear error, not a broken map.**
         `assert_overlay_bounds` exists for the same reason at the same
         layer.

    A mount would be less code and would quietly get all three wrong.
    """
    if not TRACK_PATH.exists():
        raise TrackDataError(
            f"{TRACK_PATH} is missing. It is a committed file; re-fetch it with "
            "`venv/bin/python -m backend.data_pipeline.fetch_ibtracs`."
        )
    try:
        doc = json.loads(TRACK_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise TrackDataError(f"{TRACK_PATH} is not valid JSON: {exc}") from exc

    features = doc.get("features")
    if not isinstance(features, list) or not features:
        raise TrackDataError(f"{TRACK_PATH} has no 'features' array")

    line = next(
        (f for f in features if f.get("geometry", {}).get("type") == "LineString"),
        None,
    )
    if line is None:
        raise TrackDataError(f"{TRACK_PATH} has no LineString feature (the track path)")

    line_props = line.get("properties", {})
    coordinates = line["geometry"].get("coordinates", [])

    waypoints = []
    for index, feature in enumerate(features):
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Point":
            continue
        props = feature.get("properties", {})
        coords = geometry.get("coordinates", [])
        if len(coords) < 2:
            raise TrackDataError(
                f"waypoint {index}: Point geometry needs [lon, lat], got {coords!r}"
            )
        # The fetch script writes 0.0 for a blank USA_WIND, so 0 is not a
        # measurement here — it is the absence of one. Reported as null.
        wind_kt = props.get("usa_wind_kt")
        reported = wind_kt is not None and float(wind_kt) > 0
        waypoints.append(
            {
                "sequence": len(waypoints),
                "timestamp": _parse_track_timestamp(props.get("iso_time"), index),
                "latitude": float(coords[1]),
                "longitude": float(coords[0]),
                "wind_kt": float(wind_kt) if reported else None,
                "wind_kmph": round(float(wind_kt) * KNOTS_TO_KMPH, 1) if reported else None,
                # Explicit rather than inferred from a null, so a client cannot
                # accidentally treat "absent" as "zero" by reaching for the
                # wrong field.
                "wind_reported": reported,
            }
        )

    if not waypoints:
        raise TrackDataError(f"{TRACK_PATH} has a LineString but no Point features")

    # The LineString and the Points are two views of one track. If they
    # disagree in length the file is inconsistent and a client would draw a
    # path that does not pass through the markers it also drew.
    if len(coordinates) != len(waypoints):
        raise TrackDataError(
            f"{TRACK_PATH}: LineString has {len(coordinates)} positions but there "
            f"are {len(waypoints)} Point features — the two views disagree"
        )

    # Chronological order is the whole point of a track. IBTrACS rows are
    # already sorted by the fetch script, but that is a property of a
    # generator, not of the artefact, and a hand-edited file need not keep it.
    waypoints.sort(key=lambda w: w["timestamp"])

    return {
        "name": line_props.get("name"),
        "season": line_props.get("season"),
        "source": line_props.get("source"),
        "wind_units": line_props.get("wind_units"),
        "timezone": "UTC",
        "path": [
            {"latitude": w["latitude"], "longitude": w["longitude"]} for w in waypoints
        ],
        "waypoints": waypoints,
        "waypoint_count": len(waypoints),
        "first_timestamp": waypoints[0]["timestamp"],
        "last_timestamp": waypoints[-1]["timestamp"],
        "unreported_wind_count": sum(1 for w in waypoints if not w["wind_reported"]),
        # The same block `track_payload` returns for every other cyclone, so the
        # two track endpoints have one shape and a client can consume either.
        "cyclone": {
            "cyclone_id": DEFAULT_CYCLONE_ID,
            "name": line_props.get("name"),
            "season": int(line_props.get("season") or 0),
            "basin": "NI",
            "subbasin": "BB",
            "peak_wind_kmph": max(
                (
                    w["wind_kmph"]
                    for w in waypoints
                    if w["wind_reported"] and w["wind_kmph"] is not None
                ),
                default=None,
            ),
            "waypoint_count": len(waypoints),
        },
        "disclosure": (
            "Historical best-track positions for the case-study cyclone, "
            "pre-fetched from IBTrACS and committed to data/remal_track.geojson. "
            "Nothing here is fetched live (Rules.md). Positions are 3-hourly "
            "best-track fixes, not a forecast, and the track is a record of what "
            "happened — it is not this service's prediction for any other storm. "
            "USA_WIND is the IBTrACS knots column; a fix with no reported wind "
            "carries wind_kt: null and wind_reported: false, which is NOT the "
            "same as 0 knots. The source file writes a blank USA_WIND as 0.0, so "
            "this endpoint is what separates 'not reported' from 'calm'."
        ),
    }


def track_payload(record: CycloneRecord) -> dict:
    """The `/track` shape for any catalogue record.

    **Why this exists separately from `load_track()`.** `/track` serves the
    committed GeoJSON, which carries Remal's winds in exact knots. The catalogue
    stores `wind_kmph` rounded to one decimal, so rebuilding a track from it
    would report 59.9892 kt for a storm IBTrACS records at exactly 60. For the
    case study the file is the better source and `/cyclones/{id}/track` serves
    the same payload when asked for Remal, so the two endpoints cannot disagree.
    For any other storm there is no file, and this is the only source there is.

    `wind_kt` is reconstructed from kmph and rounded to one decimal, which is
    exact for every value IBTrACS publishes in whole knots.
    """
    waypoints = []
    for index, fix in enumerate(record.waypoints):
        reported = fix.wind_reported and fix.wind_kmph is not None
        waypoints.append(
            {
                "sequence": index,
                "timestamp": _iso_z(fix.iso_time),
                "latitude": fix.latitude,
                "longitude": fix.longitude,
                "wind_kt": round(fix.wind_kmph / KNOTS_TO_KMPH, 1) if reported else None,
                "wind_kmph": fix.wind_kmph if reported else None,
                "wind_reported": reported,
                "nature": fix.nature,
            }
        )
    return {
        "name": record.name,
        "season": str(record.season),
        "source": "IBTrACS v04r01",
        "wind_units": "knots",
        "timezone": "UTC",
        "path": [
            {"latitude": w["latitude"], "longitude": w["longitude"]} for w in waypoints
        ],
        "waypoints": waypoints,
        "waypoint_count": len(waypoints),
        "first_timestamp": waypoints[0]["timestamp"] if waypoints else None,
        "last_timestamp": waypoints[-1]["timestamp"] if waypoints else None,
        "unreported_wind_count": sum(1 for w in waypoints if not w["wind_reported"]),
        "cyclone": {
            "cyclone_id": record.cyclone_id,
            "name": record.name,
            "season": record.season,
            "basin": record.basin,
            "subbasin": record.subbasin,
            "peak_wind_kmph": record.peak_wind_kmph,
            "waypoint_count": len(record.waypoints),
        },
        "disclosure": (
            "Historical best-track positions from IBTrACS v04r01, a record of what "
            "happened and not a forecast for any storm. USA_WIND is the IBTrACS "
            "knots column; a fix with no reported wind carries wind_kt: null and "
            "wind_reported: false, which is NOT the same as 0 knots. This track is "
            "not an input to the surge figure, which comes from the deterministic "
            "law 1.2 x (wind/115)^2."
        ),
    }


@app.get("/cyclones")
def list_cyclones() -> dict:
    """Every historical cyclone the app can address, and where the list came from.

    Sorted case study first, then newest first, so the default is always the
    first thing a client sees and the rest read as a chronology.
    """
    historical = registry().historical()
    by_id = {record.cyclone_id: record for record in historical}
    default_id = registry().default_cyclone_id()

    def sort_key(record: CycloneRecord) -> tuple:
        return (0 if record.cyclone_id == default_id else 1, -record.season)

    cyclones = []
    for record in sorted(historical, key=sort_key):
        cyclones.append(
            {
                "cyclone_id": record.cyclone_id,
                "name": record.name,
                "season": record.season,
                "basin": record.basin,
                "subbasin": record.subbasin,
                "peak_wind_kmph": record.peak_wind_kmph,
                "waypoint_count": len(record.waypoints),
                "is_case_study": record.cyclone_id == default_id,
            }
        )

    return {
        "source": registry().describe(),
        # UTC, like every other stamp in the service. `datetime.now()` returned
        # naive local time here, so this field was the only one whose offset was
        # machine-dependent — `/live-cyclone` reports `Z`, `/advisory` reports
        # `+00:00`, and a client comparing this against either had to guess the
        # server's timezone.
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "count": len(cyclones),
        "default_cyclone_id": default_id,
        "limitation": CATALOGUE_LIMITATION,
        "cyclones": cyclones,
    }


@app.get("/cyclones/{cyclone_id}/track")
def get_cyclone_track(cyclone_id: str) -> dict:
    """One cyclone's track, in the same shape `/track` returns.

    **Remal is served from the committed GeoJSON**, which is the same payload
    `/track` returns, so the two endpoints are literally the same code path for
    the case study and cannot disagree. Every other storm is built from the
    catalogue, which is the only source there is for it.

    An unknown id is a 400 naming the catalogue size, never a silent fall back to
    the default — a client that mistyped a storm id should be told, not shown a
    different storm's track.
    """
    record = registry().get(cyclone_id)
    if record is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown cyclone_id {cyclone_id!r}. The catalogue holds "
                f"{len(registry().historical())} North Indian Ocean storms; "
                f"call GET /cyclones for the list."
            ),
        )
    if cyclone_id == DEFAULT_CYCLONE_ID:
        return load_track()
    return track_payload(record)


@app.get("/scenarios")
def list_scenarios(
    cyclone_id: Annotated[str | None, Query(description="Cyclone these scenarios apply to")] = None,
) -> dict:
    """The strengths a client may ask for, and what each one means in kmph.

    Scoped to a cyclone because one of them is that cyclone's *own* observed
    intensity, which is a different number for every storm. The band scenarios
    are the same for all of them; the `observed` one is not, and a client
    comparing two cyclones needs to know which is which.
    """
    if cyclone_id is not None and registry().get(cyclone_id) is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown cyclone_id {cyclone_id!r}. The catalogue holds "
                f"{len(registry().historical())} North Indian Ocean storms; "
                f"call GET /cyclones for the list."
            ),
        )
    record = registry().get(cyclone_id or DEFAULT_CYCLONE_ID)
    scenarios = scenarios_for(record)

    return {
        "cyclone_id": record.cyclone_id,
        "cyclone_name": record.name,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scenarios": [
            {
                "scenario_id": scenario.scenario_id,
                "label": scenario.label,
                "wind_kmph": scenario.wind_kmph,
                "imd_category": scenario.imd_category,
                "kind": scenario.kind,
                "wind_is_band_midpoint": scenario.wind_is_band_midpoint,
                "limitation": scenario.limitation,
            }
            for scenario in scenarios
        ],
        "limitation": (
            "Scenario strengths for "
            f"{record.name} {record.season}. The band scenarios are IMD's "
            "classification thresholds at their midpoints; `observed` is this "
            "cyclone's own peak and is a different number for every storm. "
            "Every surge figure comes from the deterministic law "
            "1.2 x (wind/115)^2 and none of these is a forecast."
        ),
    }


@app.get("/live-cyclone")
def get_live_cyclone() -> dict:
    """The live state, reported as a state rather than an error.

    **Never a 5xx for an unreachable source.** A feed being down is a fact about
    the feed, not a fault in this service, and a client that cannot reach us
    because we returned 500 has learned nothing. The three statuses are kept
    apart because they mean different things: `available` means a live fix was
    parsed, `no_active_storm` means the source answered and there is nothing
    active, and `live_unavailable` means no trustworthy answer arrived.

    **Never a historical stand-in.** When the status is not `available` the
    `cyclone` field is `null`, always. Substituting Remal for a live storm would
    be the one unforgivable substitution in this system: it would put a real
    historical cyclone on a screen labelled as now.
    """
    status = registry().live_status()
    return {
        "status": status.status,
        "source": status.source,
        "http_status": status.http_status,
        "reason": status.reason,
        "checked_at": status.checked_at,
        "endpoints": status.endpoints,
        "cyclone": status.cyclone,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "limitation": (
            "Live cyclone state from the ATCF best-track feeds. This is a probe of "
            "a live source, not a forecast, and it is never substituted with a "
            "historical or case-study cyclone: when no live fix is available the "
            "cyclone field is null. The historical list at /cyclones and the "
            "deterministic storm-surge simulation are unaffected by this status."
        ),
    }


def _comparison_entry(ctx: ScenarioContext) -> dict:
    """One cyclone at one scenario, reduced to the figures a comparison needs.

    Every number here is one the app computed for this exact pair — no figure is
    carried across from another cyclone or another scenario, which is the whole
    reason the comparison exists.
    """
    flood = flood_for_scenario(ctx)
    exposure_result = compute_exposure(flood.frames[-1].geometry)
    payload = exposure_result.to_dict()
    scenario = ctx.resolve(registry().get(ctx.cyclone_id))
    record = registry().get(ctx.cyclone_id)
    return {
        "cyclone_id": ctx.cyclone_id,
        "name": record.name if record else None,
        "season": record.season if record else None,
        "scenario_id": ctx.scenario_id,
        "scenario_kind": scenario.kind,
        "wind_kmph": scenario.wind_kmph,
        "imd_category": scenario.imd_category,
        # From the deterministic law, never from the scenario: a scenario is a
        # wind, and the surge is a function of that wind.
        "surge_m": round(surge_for_wind(scenario.wind_kmph), 4),
        "flood_area_km2": round(flood.frames[-1].land_area_km2, 2),
        "hospitals_exposed": payload["hospitals"]["count"],
        "substations_exposed": payload["substations"]["count"],
        "roads_cut_off": payload["roads"]["count"],
        "unreported_wind_fixes": (
            sum(1 for w in record.waypoints if not w.wind_reported) if record else None
        ),
    }


@app.get("/comparison")
def compare_cyclones(
    category: Annotated[int, Query(ge=0, le=6, description="IMD category index 0-6")],
    cyclone_ids: Annotated[
        str | None,
        Query(description="Comma-separated IBTrACS SIDs. Absent means the case study."),
    ] = None,
    scenario_id: Annotated[
        str | None, Query(description="`cat0`-`cat6`, or `observed`. Wins over `category`.")
    ] = None,
) -> dict:
    """The same scenario applied to several cyclones, side by side.

    The point is the delta, and a delta is only meaningful between figures that
    were computed the same way. So every entry is built by one helper from one
    `(cyclone_id, scenario_id)` pair, and the deltas are taken between entries
    in this response rather than against anything a client remembered.

    **A single cyclone has no delta.** Emitting a zero delta for one storm would
    present a comparison that was never made.
    """
    if cyclone_ids:
        ids = [part.strip() for part in cyclone_ids.split(",") if part.strip()]
    else:
        ids = [DEFAULT_CYCLONE_ID]

    unknown = [cyclone_id for cyclone_id in ids if registry().get(cyclone_id) is None]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown cyclone_id {unknown[0]!r}. The catalogue holds "
                f"{len(registry().historical())} North Indian Ocean storms; "
                f"call GET /cyclones for the list."
            ),
        )

    entries = [_comparison_entry(_context(category, cyclone_id, scenario_id)) for cyclone_id in ids]

    deltas: dict = {"between": ids}
    if len(entries) == 2:
        left, right = entries
        deltas["note"] = (
            f"{left['name']} {left['season']} against {right['name']} {right['season']}, "
            f"both at {left['scenario_id']} ({left['wind_kmph']:.0f} kmph)."
        )
        for field in (
            "surge_m",
            "flood_area_km2",
            "hospitals_exposed",
            "substations_exposed",
            "roads_cut_off",
        ):
            deltas[f"{field}_delta"] = round(left[field] - right[field], 4)
    elif len(entries) == 1:
        deltas["note"] = (
            "One cyclone, so there is nothing to compare it against. Add a second "
            "cyclone_id to get deltas."
        )
    else:
        deltas["note"] = (
            f"{len(entries)} cyclones. Deltas are reported for exactly two, because "
            "a delta between three storms is not a number."
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scenario_id": entries[0]["scenario_id"],
        "cyclones": entries,
        "deltas": deltas,
        "limitation": (
            "Each cyclone computed independently at the same scenario strength, so "
            "the deltas are between figures produced the same way. The surge figure "
            "comes from the deterministic law 1.2 x (wind/115)^2 and is not a "
            "forecast; a cyclone's own observed intensity is a different scenario "
            "from these band midpoints."
        ),
    }


@app.get("/track")
def get_track() -> dict:
    """Cyclone Remal's historical track: path, waypoints, wind, disclosure.

    Real observed data for the case study, from the committed IBTrACS
    extract. It is deliberately not keyed by category: the track is a
    historical fact and does not vary with the slider, so there is no
    parameter to get wrong and nothing to refetch when the user drags.
    """
    try:
        return load_track()
    except TrackDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    category: Annotated[int, Query(ge=0, le=6, description="IMD category index 0-6")],
    cyclone_id: Annotated[
        str | None, Query(description="IBTrACS SID. Absent means the default case study.")
    ] = None,
    scenario_id: Annotated[
        str | None, Query(description="`cat0`-`cat6`, or `observed`. Wins over `category`.")
    ] = None,
) -> dict:
    """Flood polygon at a category's representative intensity."""
    ctx = _context(category, cyclone_id, scenario_id)
    result = flood_for_scenario(ctx)
    payload = result.to_feature_collection()
    return {
        **_provenance(ctx),
        **_category_header(
            category,
            ctx,
            wind_kmph=ctx.wind_kmph(registry().get(ctx.cyclone_id)),
            is_band_midpoint=ctx.resolve(
                registry().get(ctx.cyclone_id)
            ).wind_is_band_midpoint,
        ),
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
            "is modelled. The flood model spreads water at most about 0.5 km inland "
            "from the water's edge, a limit of the algorithm it follows, so flooded "
            "area and exposure counts are likely understated."
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
    category: Annotated[int, Query(ge=0, le=6, description="IMD category index 0-6")],
    cyclone_id: Annotated[
        str | None, Query(description="IBTrACS SID. Absent means the default case study.")
    ] = None,
    scenario_id: Annotated[
        str | None, Query(description="`cat0`-`cat6`, or `observed`. Wins over `category`.")
    ] = None,
) -> dict:
    """Which hospitals, substations and roads the flood reaches."""
    ctx = _context(category, cyclone_id, scenario_id)
    flood = flood_for_scenario(ctx)
    result = compute_exposure(flood.frames[-1].geometry)
    payload = result.to_dict()
    return {
        **_provenance(ctx),
        **_category_header(
            category,
            ctx,
            wind_kmph=ctx.wind_kmph(registry().get(ctx.cyclone_id)),
            is_band_midpoint=ctx.resolve(
                registry().get(ctx.cyclone_id)
            ).wind_is_band_midpoint,
        ),
        "cyclone": _cyclone_block(registry().get(ctx.cyclone_id)),
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
    category: Annotated[int, Query(ge=0, le=6, description="IMD category index 0-6")],
    origin: str = Query(..., description="Locality id from /localities"),
    cyclone_id: Annotated[
        str | None, Query(description="IBTrACS SID. Absent means the default case study.")
    ] = None,
    scenario_id: Annotated[
        str | None, Query(description="`cat0`-`cat6`, or `observed`. Wins over `category`.")
    ] = None,
) -> dict:
    """A flood-free route from a locality to its assigned shelter.

    Unreachable is a real answer here — a high surge severs the delta — so it
    returns 200 with `reachable: false` and a reason, not a 404.
    """
    ctx = _context(category, cyclone_id, scenario_id)
    locality = get_locality(origin)
    if locality is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown origin '{origin}'. Call GET /localities for valid ids."
            ),
        )

    flood = flood_for_scenario(ctx)
    allocation = allocation_for_scenario(ctx)
    shelter, assigned = _shelter_for(locality, allocation, shelters_for_scenario(ctx))

    route = safe_route(
        build_road_graph(),
        flood.frames[-1].geometry,
        (locality.lon, locality.lat),
        (shelter.lon, shelter.lat),
    )
    if not route.reachable:
        route = _diagnose_unreachable(route, locality, shelter, flood)
    return {
        **_category_header(
            category,
            ctx,
            wind_kmph=ctx.wind_kmph(registry().get(ctx.cyclone_id)),
            is_band_midpoint=ctx.resolve(
                registry().get(ctx.cyclone_id)
            ).wind_is_band_midpoint,
        ),
        **_provenance(ctx),
        "origin": locality.to_dict(),
        "shelter": shelter.to_dict(),
        "shelter_assignment_basis": assigned,
        "shelter_status": shelter_dataset_status(),
        "capacity_basis": _capacity_basis(ctx),
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
    category: Annotated[int, Query(ge=0, le=6, description="IMD category index 0-6")],
    cyclone_id: Annotated[
        str | None, Query(description="IBTrACS SID. Absent means the default case study.")
    ] = None,
    scenario_id: Annotated[
        str | None, Query(description="`cat0`-`cat6`, or `observed`. Wins over `category`.")
    ] = None,
) -> dict:
    """Capacity-aware shelter assignment per locality (transportation LP)."""
    ctx = _context(category, cyclone_id, scenario_id)
    flood = flood_for_scenario(ctx)
    populations = populations_for_scenario(ctx)
    # Same cached result /routes reads, so the two endpoints cannot drift
    # apart on which shelters exist or who is assigned where.
    result = allocation_for_scenario(ctx)
    return {
        **_category_header(
            category,
            ctx,
            wind_kmph=ctx.wind_kmph(registry().get(ctx.cyclone_id)),
            is_band_midpoint=ctx.resolve(
                registry().get(ctx.cyclone_id)
            ).wind_is_band_midpoint,
        ),
        **_provenance(ctx),
        "final_land_area_km2": payload_counts_land(flood),
        "localities_evaluated": len(populations),
        "allocation": result["assignment"],
        "shelter_loads": result["shelter_loads"],
        "unmet_demand": result["unmet_demand"],
        "total_person_km": result.get("total_person_km"),
        "message": result["message"],
        "shelter_status": result["status"],
        "capacity_basis": _capacity_basis(ctx),
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


def _diagnose_cause_and_reason(route, locality, shelter, flood) -> tuple[str, str]:
    """The same branch the reason comes from, plus the structural cause of it.

    Returns `(cause, reason)` where cause is "flood" — water cut the route — or
    "road_data" — the committed extract never connected origin and shelter.
    Nothing here works by matching reason text; the cause is the branch the
    reason is being computed from. Used by `_origin_facts` to tag advisory
    entries with their actual cause, and by `_diagnose_unreachable`, which keeps
    its old signature for `/routes`.
    """
    graph = build_road_graph()
    origin_node = _nearest_node(graph, (locality.lon, locality.lat))
    shelter_node = _nearest_node(graph, (shelter.lon, shelter.lat))
    components = _component_index()

    flood_geom = _flood_shape(flood)
    has_flood = flood_geom is not None and not flood_geom.is_empty

    if origin_node is None or shelter_node is None:
        return ("road_data", "no road network is loaded")
    if components.get(origin_node) != components.get(shelter_node):
        return (
            "road_data",
            "no route: the committed OSM extract does not connect these two "
            "points. roads.geojson holds arterials and delta_roads.geojson "
            "covers the southern delta only, so many inland towns have no "
            "path to the delta. This is road-data coverage, not flooding"
            + ("" if has_flood else " (there is no flood at this category)"),
        )
    if not has_flood:
        return (
            "road_data",
            "no route, and there is no flood at this category — the origin or "
            "shelter could not be matched to the road network",
        )
    # The two points are connected in the dry network, so only water can
    # explain it; routing's own reason is the truthful one here.
    return ("flood", route.reason)


def _diagnose_unreachable(route, locality, shelter, flood):
    """Replace a misleading `reason` with the actual cause.

    `routing.safe_route` reports the same string whether water closed the
    route or the committed road extract simply has no path between the two
    points. The second is common here: `roads.geojson` holds arterials and
    `delta_roads.geojson` covers only the southern delta, so inland towns
    like Canning are not connected to the delta at all. Saying "cut off" at
    zero surge would read as a flood warning that does not exist.

    Thin wrapper over `_diagnose_cause_and_reason` that discards the cause;
    same return type and wording.
    """
    _, reason = _diagnose_cause_and_reason(route, locality, shelter, flood)
    return replace(route, reason=reason)


def _capacity_basis(ctx: ScenarioContext) -> dict:
    """How the shelter capacities in this response were arrived at.

    The single most misreadable number in the API: a client that renders
    "Shelter C: 41,765 capacity" without this block is presenting a derived
    placeholder as a surveyed facility.
    """
    shelters = shelters_for_scenario(ctx)
    return {
        "shelters_are_real": any(not s.is_demo_data for s in shelters),
        "total_capacity_people": sum(s.capacity_people for s in shelters),
        "estimated_demand_people": sum(
            n.population for n in populations_for_scenario(ctx)
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


class GeminiQuotaError(RuntimeError):
    """The daily free-tier request limit is spent. Retry-After would be a lie."""


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


def _is_quota_error(exc: Exception) -> bool:
    """Is this a spent daily quota rather than a busy model?

    Deliberately disjoint from `_is_capacity_error`. The two are opposites in
    every way a client cares about: a capacity block clears in about a minute
    and the identical request is worth repeating, while a spent daily quota
    does not clear until midnight Pacific. Sending the same advice for both
    either wastes a minute of a user's time or sends them to bed for eight
    hours, so they get separate outcomes and separate tests.

    Matched on the 429 code and the `RESOURCE_EXHAUSTED` status word, because
    the SDK surfaces both depending on how the response came back. A 400 or a
    malformed-response error is ours to fix and must not be filed as a limit.
    """
    if getattr(exc, "code", None) == 429:
        return True
    status = (getattr(exc, "status", None) or "").upper()
    if status == "RESOURCE_EXHAUSTED":
        return True
    return "RESOURCE_EXHAUSTED" in str(exc).upper()


#: What a client is told when the daily limit is spent. Named once, because
#: the endpoint, the tests and the mobile copy all have to say the same thing
#: and a quota message that drifts into "please try again later" defeats the
#: entire point of having a distinct outcome.
GEMINI_DAILY_LIMIT_MESSAGE = (
    "The Gemini free-tier daily request limit has been reached. Advisories are "
    "blocked until the quota resets at midnight Pacific, so retrying now will "
    "not help and each attempt risks the rest of the day's quota. This is a "
    "usage limit, not a fault in the simulation or the app: every other "
    "endpoint here is unaffected and still serving. Generated advisories "
    "captured earlier are unaffected and can still be shown."
)


def _with_capacity_retry(fn, *args, **kwargs) -> tuple[object, int]:
    """`fn`, retried across capacity blocks. The one ladder, shared by both
    Gemini endpoints.

    Extracted from `_generate_with_capacity_retry` when `/risk-analyst` was
    added: the plan requires that endpoint to reuse this ladder and this error
    taxonomy *verbatim*, and a second copy of the loop is a second place for the
    two to disagree about how many attempts a busy model gets. `/advisory` and
    `/risk-analyst` differ in what they call and in the prose they return, not
    in how they handle a model that is at capacity.

    Returns the value and the number of attempts it took, so a handler can
    report how hard it tried instead of a client guessing from a latency. The
    only error this raises itself is exhausted-retry exhaustion.
    """
    last: ServerError | None = None
    waited = 0
    for attempt in range(1, CAPACITY_MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs), attempt
        except Exception as exc:
            # A spent daily quota is checked first and re-raised immediately,
            # ahead of the capacity branch. It arrives as a `ClientError` 429,
            # which is not a `ServerError` at all — so before this clause it
            # never entered this handler and fell through to the endpoint's
            # blanket `except Exception`, becoming a 502. Retrying it would
            # also be actively harmful: each attempt spends a little more of a
            # limit that has already run out.
            if _is_quota_error(exc):
                raise GeminiQuotaError(GEMINI_DAILY_LIMIT_MESSAGE) from exc
            if not isinstance(exc, ServerError):
                raise
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
    # The global is read here, at call time, so a test that patches
    # `main.generate_advisory` is still patching what the ladder invokes.
    return _with_capacity_retry(generate_advisory, *args, **kwargs)


@app.post("/advisory")
def advisory(
    category: Annotated[int, Query(ge=0, le=6, description="IMD category index 0-6")],
    origin: str = Query(..., description="Locality id from /localities"),
    cyclone_id: Annotated[
        str | None, Query(description="IBTrACS SID. Absent means the default case study.")
    ] = None,
    scenario_id: Annotated[
        str | None, Query(description="`cat0`-`cat6`, or `observed`. Wins over `category`.")
    ] = None,
) -> dict:
    """Synthesise a district advisory from the simulation outputs (Module D).

    One of three endpoints that reach the network — with `/risk-analyst` and
    `/live-cyclone` — and the only one that calls a *model*, and only ever
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
    ctx = _context(category, cyclone_id, scenario_id)
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
    surge_payload = surge_zone(
        category, cyclone_id=ctx.cyclone_id, scenario_id=ctx.scenario_id
    )
    exposure_payload = exposure(
        category, cyclone_id=ctx.cyclone_id, scenario_id=ctx.scenario_id
    )
    allocation_payload = allocation(
        category, cyclone_id=ctx.cyclone_id, scenario_id=ctx.scenario_id
    )

    facts = _origin_facts(ctx, locality)
    context = _origin_context(facts)

    try:
        result, gemini_calls = _generate_with_capacity_retry(
            surge_payload, exposure_payload, allocation_payload, context=context,
            cyclone=_cyclone_block(registry().get(ctx.cyclone_id)),
        )
    except GeminiQuotaError as exc:
        # Before the capacity handler and the blanket `except Exception`. A
        # spent quota is not a busy model and not a broken service, and the
        # advice is the opposite: do not retry, it resets at midnight Pacific.
        # 429 is the truthful code, and 429 without a Retry-After is a
        # deliberate omission — a header claiming a minute would be a lie, and
        # a client honouring it would poll until the reset.
        raise HTTPException(status_code=429, detail=GEMINI_DAILY_LIMIT_MESSAGE) from exc
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
                cyclone=_cyclone_block(registry().get(ctx.cyclone_id)),
            )
            gemini_calls += correction_calls
        except GeminiQuotaError as exc:
            # The correction pass spends the same daily quota, so it can hit
            # the same limit. Same 429, same message, and — deliberately — no
            # mention of the violations: the limit is the reason the user sees,
            # and mixing in a drafting detail invites them to retry.
            raise HTTPException(status_code=429, detail=GEMINI_DAILY_LIMIT_MESSAGE) from exc
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
            "cyclone_id": ctx.cyclone_id,
            "scenario_id": ctx.scenario_id,
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


class RiskAnalystRequest(BaseModel):
    """The body `POST /risk-analyst` takes.

    A body rather than query parameters because this is the one endpoint that
    is reached by a deliberate press rather than by a page loading, and a body
    is not bookmarkable — which is the point. Nothing here auto-fires.
    """

    category: int = Field(..., ge=0, le=6, description="IMD category index 0-6")
    cyclone_id: str | None = Field(
        default=None, description="IBTrACS SID. Absent means the default case study."
    )
    scenario_id: str | None = Field(
        default=None, description="`cat0`-`cat6`, or `observed`. Wins over `category`."
    )
    origin: str | None = Field(
        default=None, description="Locality id from /localities. Optional."
    )


def _comparison_partner(cyclone_id: str) -> str:
    """Which storm to set beside this one.

    The case study, because a reader already knows what Remal did and a
    comparison against a familiar reference is one a person can actually use.
    When the request **is** the case study, comparing it with itself would emit
    a column of zeros and a note that there is nothing to compare — so it falls
    to the basin's strongest other storm: informative, deterministic, and not a
    coin flip between two arbitrary ids.
    """
    if cyclone_id != DEFAULT_CYCLONE_ID:
        return DEFAULT_CYCLONE_ID
    others = [r for r in registry().historical() if r.cyclone_id != cyclone_id]
    if not others:  # pragma: no cover - the catalogue has 610 storms
        return cyclone_id
    return max(others, key=lambda r: (r.peak_wind_kmph or 0.0, r.season)).cyclone_id


@app.post("/risk-analyst")
def risk_analyst(body: RiskAnalystRequest) -> dict:
    """An evidence-grounded risk analysis, behind a deliberate press.

    Three properties, in the order they are checked:

    **The request is validated before the server configuration**, so a client
    that named an unknown cyclone hears about that even on an unconfigured
    server. The two problems are independent and reporting only the second
    hides the first — the same ordering `/advisory` uses, deliberately.

    **Configuration reuses `/advisory`'s disclosure verbatim.** A second
    config path is a second place for the two to drift, and a client that
    already handles the advisory's 503 would otherwise have to learn a second
    one for an identical cause.

    **The payloads are the ones the client can fetch itself** — `exposure()`
    and `compare_cyclones()` are called directly rather than re-derived. This
    is what stops the prose and the map disagreeing: if the analyst assembled
    its own figures, nothing downstream would notice a divergence.

    The surge figure reaches the model as the deterministic law's output, and
    the ML estimate reaches it with its own gate verdict attached. Neither is
    recomputed, rounded differently, or presented as the other.
    """
    ctx = _context(body.category, body.cyclone_id, body.scenario_id)

    locality = None
    if body.origin is not None:
        locality = get_locality(body.origin)
        if locality is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Unknown origin '{body.origin}'. Call GET /localities for "
                    f"valid ids."
                ),
            )

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

    exposure_payload = exposure(
        body.category, cyclone_id=ctx.cyclone_id, scenario_id=ctx.scenario_id
    )
    partner = _comparison_partner(ctx.cyclone_id)
    comparison_payload = compare_cyclones(
        body.category,
        cyclone_ids=f"{ctx.cyclone_id},{partner}",
        scenario_id=ctx.scenario_id,
    )

    # No feature vector, and none invented: see `baseline_estimate`. The gate
    # failed, so the shipped figure is a constant that no features can move.
    peak = baseline_estimate()

    origin_facts = None
    if locality is not None:
        facts = _origin_facts(ctx, locality)
        origin_facts = {
            "locality_id": body.origin,
            "locality_name": locality.name,
            "shelter": facts["shelter"].name,
            "route_reachable": facts.get("reachable"),
            "route_length_km": facts.get("length_km"),
        }

    # The same ladder `/advisory` uses, and the same three outcomes: a spent
    # quota is 429 with no Retry-After (retrying spends a limit already gone),
    # a busy model is retried three times and then 503 with one, and any other
    # SDK failure is 502. Before this was wired in, all three surfaced as a
    # bare non-JSON 500 — a plan violation, since Task 8 requires this endpoint
    # to reuse the ladder and the taxonomy verbatim.
    try:
        result, gemini_calls = _with_capacity_retry(
            generate_risk_analysis,
            context=ctx,
            exposure=exposure_payload,
            comparison=comparison_payload,
            peak_estimate=peak,
            advisory=None,
            origin_facts=origin_facts,
        )
    except GeminiQuotaError as exc:
        raise HTTPException(status_code=429, detail=GEMINI_DAILY_LIMIT_MESSAGE) from exc
    except GeminiCapacityError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": str(CAPACITY_RETRY_AFTER_SECONDS)},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - any SDK failure is a 502 to the client
        raise HTTPException(
            status_code=502,
            detail=f"Gemini risk analysis failed: {type(exc).__name__}: {exc}",
        ) from exc

    analysis: RiskAnalysis = result

    return {
        **_provenance(ctx),
        **_category_header(
            body.category,
            ctx,
            wind_kmph=exposure_payload["wind_kmph"],
            is_band_midpoint=exposure_payload["wind_is_band_midpoint"],
        ),
        "cyclone": _cyclone_block(registry().get(ctx.cyclone_id)),
        "analysis": analysis.model_dump(),
        # Same field name `/advisory` uses, for the same reason: HTTP calls
        # actually made. A client handling both endpoints learns one name, and
        # a reader can tell a first-try answer from one that rode out two 503s.
        "gemini_calls": gemini_calls,
        "model": ADVISORY_MODEL,
        "comparison_between": comparison_payload["deltas"]["between"],
        "peak_estimate": peak.to_dict(),
        "is_estimate": True,
        "limitation": (
            "AI-generated analysis written by "
            f"{ADVISORY_MODEL} from this service's own computed figures. It is "
            "NOT an official warning and not an IMD product, and it must not be "
            "presented as one. The storm-surge figure inside it comes from the "
            "deterministic law 1.2 x (wind/115)^2 — a screening estimate scaled "
            "from one observed event, omitting tide, pressure, bathymetry and "
            "storm size — and stays authoritative. "
        + _ml_figure_disclosure(peak)
    ),
}


def _ml_figure_disclosure(peak: StormPeakEstimate) -> str:
    """The ML-figure sentence, derived from the artefact's own gate verdict.

    Hardcoding "did not beat its own gate" would be true today and a lie the
    day a real training table makes the gate pass — the sentence must move
    with `beats_baseline`, whatever that becomes.
    """
    if not peak.beats_baseline:
        return (
            "The machine-learning figure is a flat-median baseline that did "
            "not beat its own baseline gate, is not a prediction, and is not "
            "the surge figure."
        )
    return (
        "The machine-learning figure comes from the fitted model and beat "
        "its baseline gate under leave-one-out cross-validation, so it is a "
        "genuine estimate rather than a labelled baseline. It is still not "
        "the surge figure."
    )


# --------------------------------------------------------------------------
# Per-category derived results (cached with their flood)
# --------------------------------------------------------------------------


@lru_cache(maxsize=64)
def populations_for_scenario(ctx: ScenarioContext) -> tuple[DemandNode, ...]:
    """Evacuation demand per locality at this category's flood extent.

    Each locality is passed only the buildings inside its own search radius
    (see locations.buildings_near) — the density count inside
    estimate_populations is unchanged by the pre-filter, but the work drops
    from ~50M point-in-box tests to a few million.
    """
    from .simulation.population import estimate_populations

    flood = flood_for_scenario(ctx)
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


@lru_cache(maxsize=64)
def shelters_for_scenario(ctx: ScenarioContext) -> tuple[Shelter, ...]:
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

    nodes = populations_for_scenario(ctx)
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


@lru_cache(maxsize=64)
def allocation_for_scenario(ctx: ScenarioContext) -> dict:
    """Shelter assignment for one (cyclone, scenario) pair.

    Keyed on the pair for the same reason the flood is: the populations and the
    shelter capacities both come from the flood extent, so a category-keyed entry
    is only ever correct for the storm that produced it.
    """
    return allocate_shelters(
        populations_for_scenario(ctx), list(shelters_for_scenario(ctx))
    )


def _origin_facts(ctx: ScenarioContext, locality: Locality) -> dict:
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
    flood = flood_for_scenario(ctx)
    allocation_result = allocation_for_scenario(ctx)
    shelter, basis = _shelter_for(
        locality, allocation_result, shelters_for_scenario(ctx)
    )
    route = safe_route(
        build_road_graph(),
        flood.frames[-1].geometry,
        (locality.lon, locality.lat),
        (shelter.lon, shelter.lat),
    )
    if route.reachable:
        cause = "none"
        reason = route.reason
    else:
        # One pass: the advisory never tells someone to travel a road the
        # extract simply does not contain, and it never labels a road-data
        # gap as flooding — the cause and reason come from the same branch.
        cause, reason = _diagnose_cause_and_reason(route, locality, shelter, flood)
    return {
        "locality": locality,
        "shelter": shelter,
        "shelter_basis": basis,
        "reachable": route.reachable,
        "cause": cause,
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
    elif facts.get("cause") == "road_data":
        priority = "HIGH"
        reasoning = (
            f"The requesting locality. The committed road extract does not "
            f"connect it to its assigned shelter ({facts['shelter'].name}) — "
            f"this is a data gap, not a flood finding. Call for off-network "
            f"planning should name the gap plainly rather than claiming "
            f"flooding. ({_as_sentence(facts['reason'])})"
        )
    else:
        # cause == "flood": the dry network is connected and water closed the route.
        priority = "CRITICAL"
        reasoning = (
            f"The requesting locality. It is UNREACHABLE at this intensity: "
            f"{_as_sentence(facts['reason'])} Evacuation cannot proceed along the "
            f"mapped road network from here, so movement must be planned "
            f"off-network."
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
