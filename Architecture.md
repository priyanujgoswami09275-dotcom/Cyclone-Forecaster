# Architecture.md — Cyclone Impact & Infrastructure Vulnerability Forecaster

The technical shape of the system as it exists today: layers, data flow, repo
structure, and the API contract. For the reference implementations (surge law,
flood BFS, routing, LP allocation), see the "Reference code" section in
CLAUDE.md/AGENTS.md — this file defines the contract, that file holds the code.

## Data flow

```
Data sources (IBTrACS Remal track + the full NI catalogue, OSM infra,
              GEE SRTM DEM, historical cyclone dataset, ATCF live feeds,
              Open-Meteo*)
        |
Surge model — anchored quadratic scaling: 1.2 x (wind/115)^2.
              NOT a trained regression (see README.md "Corrections").
        |
Simulation engine:
  - Flood propagation (BFS cellular automaton, time-stepped)
  - Evacuation routing (graph search over live road network)
  - Shelter allocation (linear programming / transportation problem)
        |
Dynamic cyclone layer:
  - Historical catalogue — 610 NI storms, BASIN == 'NI' at ingestion
  - Scenarios per storm — observed (only where a track is committed) + 7 bands
  - Live ATCF probe — 4 configurable endpoints; live_unavailable is the
    expected default; never backfilled with a historical storm
  - Storm-peak-intensity model — gate FAILED vs the flat median, so the
    median ships, labelled median_baseline
        |
Gemini AI — /advisory + /risk-analyst, synthesizes from computed outputs
        |
Mobile app (Expo / React Native) — map screen + advisory modal, both
           the native (Leaflet WebView) and Web (projected SVG) variants

* Open-Meteo is implemented and tested under backend/weather/ but is
  wired to no route — see README "Weather context".
```

## Layers

| Layer | Component | Responsibility |
|---|---|---|
| Data | IBTrACS (v04r00 fetcher, v04r01 catalogue), OSM (Overpass), GEE (SRTM DEM), ATCF, Open-Meteo | Raw inputs, pre-fetched once and committed as static files; ATCF/Open-Meteo are live probes |
| ML | Storm-peak-intensity linear model + flat-median baseline | Predicts each storm's peak `USA_WIND`. **Gate FAILED** — the baseline ships; see `backend/ml/` and `data/ml/` |
| Simulation | BFS flood propagation, `osmnx`/`networkx` routing, `scipy.optimize` LP | The actual computation — flood spread over time, safe routes, shelter assignment |
| Backend | FastAPI | Orchestrates the above behind a REST API |
| AI | google-genai SDK + Gemini (`gemini-3.8-flash`) | Synthesizes the district advisory and the risk analysis from structured outputs |
| Mobile | Expo / React Native | The UI — map, chips, picker, advisory modal (native + Web variants) |
| Deployment | Vercel Hobby (`app.py` + `vercel.json`) | Hosts the FastAPI backend; `render.yaml` is a retained fallback |

## Repo structure

```
/data                      # precomputed outputs, committed (not fetched live)
  remal_track.geojson
  hospitals.geojson  substations.geojson  roads.geojson  dem.tif
  places.geojson  buildings.csv.gz  delta_roads.geojson  shelters.json
  /cyclones/catalogue.json     # 610 NI storms
  /ml/storm_peak_intensity.json# the measured ML report (gate failed)
  /overlays/                   # pre-rendered flood rasters (display only)
  /basemap/                    # land/water raster for the Web SVG map
/backend
  /data_pipeline           # ingestion fetchers (IBTrACS, Overpass, GEE, OSM infra)
  /simulation               # Module B: flood, routing, allocation, shelters, dem
  /cyclones                 # normalized record, registry, ATCF, live, scenarios
  /ml                       # storm-peak-intensity model
  /weather                  # Open-Meteo client (no route)
  /ai                        # Module D: Gemini schema + prompt
  /tools                     # render_overlays, render_basemap
  /experiments               # abandoned surge regression, kept as a record
  main.py                    # Module C: FastAPI app (17 routes)
/mobile                     # Module E: Expo app
  /components                # MapScreen.tsx (native), MapScreen.web.tsx (Web),
                            #   LeafletMap.tsx, CyclonePicker, ScenarioComparePanel,
                            #   RiskAnalystPanel, panels, chips
  /tests                     # node --test suite
/tests                      # pytest suite
AGENTS.md, CLAUDE.md, GEMINI.md   # CLAUDE.md content; AGENTS/GEMINI are symlinks
MEMORY.md                          # living session state + bootstrap prompt
PRD.md, Architecture.md, Rules.md, Task.md   # this document set
```

## API contract

Seventeen routes (verified against `backend/main.py`). All scoped getters take
`category`, and the scoped ones take optional `cyclone_id` / `scenario_id`,
defaulting to the case study when omitted.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service banner + route index |
| GET | `/health` | DEM shape, graph size, `advisory_ready`, `advisory_model` |
| GET | `/categories` | The seven IMD bands, the Remal preset, the anchor, the limitation |
| GET | `/overlays` | Pre-rendered flood PNGs per category (display shortcut only) |
| GET | `/cyclones` | 610 NI storms, Remal first, 1970–2026, `is_case_study` |
| GET | `/cyclones/{id}/track` | One storm's track (the case study and others share a parser) |
| GET | `/scenarios` | A storm's scenarios (`observed` only where a track is committed) |
| GET | `/live-cyclone` | Live ATCF probe, or explicit `live_unavailable` with statuses |
| GET | `/comparison` | Two scenarios side by side, with the delta |
| GET | `/track` | The Remal case-study track (no query params — takes the default) |
| GET | `/localities` | Locality list (45 in, 32 excluded by scoping) |
| GET | `/surge-zone?category={0-6}` | Surge height, method, anchor, flood polygon |
| GET | `/exposure?category={0-6}[&cyclone_id=&scenario_id=]` | Submerged hospitals/substations/roads |
| GET | `/routes?category={0-6}&origin={id}[&cyclone_id=&scenario_id=]` | Safe route + assigned shelter |
| GET | `/allocation?category={0-6}[&cyclone_id=&scenario_id=]` | Shelter assignment LP, loads, unmet demand |
| POST | `/advisory` | Gemini advisory synthesized from the simulation outputs |
| POST | `/risk-analyst` | Gemini risk narrative over the same inputs; quota/capacity surfaced |

Notes:
- A per-(cyclone_id, scenario_id) cache keys every scenario result; changing
  either input changes the result (`tests/test_cache_isolation.py`).
- `GET /track` deliberately takes no parameters. Passing `?cyclone_id=` would
  be silently ignored by FastAPI and return Remal — the app routes an id to
  `/cyclones/{id}/track` instead.
- `/advisory` builds its payloads from the request's cyclone/scenario context;
  `/risk-analyst` does the same.

## Request sequence for one user interaction

1. App boots and fetches `/cyclones` and `/live-cyclone`.
2. User picks a storm from the CyclonePicker (Remal by default) and a strength
   chip (four bands). Changing either re-scopes `/exposure`, `/routes`,
   `/allocation` with the new `cyclone_id` / `scenario_id`.
3. The track redraws from `/cyclones/{id}/track`; the masthead names the
   selected storm.
4. Optional: "Compare scenarios" fetches two scenarios of the selected storm
   (the second looked up via `/scenarios`). "Generate analysis" POSTs to
   `/risk-analyst`.
5. User taps "Generate Advisory." The app POSTs to `/advisory`; the returned
   `DistrictAdvisory` renders in the modal; SMS draft is copyable.

## Tech stack summary

| Tool | Used for |
|---|---|
| FastAPI + Uvicorn | API server |
| rasterio, geopandas, shapely | DEM reading, polygon geometry, spatial intersections |
| scipy (`linprog`) | Shelter allocation LP |
| scikit-learn | Storm-peak-intensity linear model (gate failed) |
| osmnx + networkx | Road graph, shortest safe path |
| google-genai SDK + `gemini-3.8-flash` | Advisory + risk-analyst generation |
| pydantic | Structured Gemini output |
| Expo + react-native-webview (Leaflet) | Native map UI |
| Projected SVG | Web map UI (no map library) |
| Vercel Hobby (`app.py` + `vercel.json`) | Backend hosting |

Full detail and code for each of these lives in CLAUDE.md/AGENTS.md.
