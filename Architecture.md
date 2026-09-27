# Architecture.md — Cyclone Impact & Infrastructure Vulnerability Forecaster

This defines the technical shape of the system: layers, data flow, repo
structure, and API contract. For the actual reference implementations
(surge model training code, flood BFS, routing, LP allocation), see the
"Reference code" section in CLAUDE.md/AGENTS.md — this file defines the
contract, that file holds the code.

## Data flow

```
Data sources (track, OSM infra, DEM, historical cyclone dataset)
        |
Surge ML model — trained regression, not a lookup table
        |
Simulation engine:
  - Flood propagation (BFS cellular automaton, time-stepped)
  - Evacuation routing (graph search over live road network)
  - Shelter allocation (linear programming / transportation problem)
        |
Gemini AI — synthesizes the district advisory from all computed outputs
        |
Mobile app (Expo / React Native) — displays everything
```

## Layers

| Layer | Component | Responsibility |
|---|---|---|
| Data | IBTrACS, OSM (Overpass), GEE (SRTM DEM) | Raw inputs, pre-fetched once, committed as static files |
| ML | scikit-learn regression | Predicts surge height from storm parameters, trained on real historical points |
| Simulation | BFS flood propagation, osmnx/networkx routing, scipy.optimize LP | The actual computation — flood spread over time, safe routes, shelter assignment |
| Backend | FastAPI | Orchestrates the above behind a REST API |
| AI | google-genai SDK + Gemini | Synthesizes a human-readable advisory from the simulation's structured output |
| Mobile | Expo / React Native | The UI — map, slider, advisory modal |
| Deployment | Render or Railway | Hosts the FastAPI backend |

## Repo structure

```
/data                      # precomputed outputs, committed (not fetched live)
  remal_track.geojson
  hospitals.geojson
  substations.geojson
  roads.geojson
  dem.tif
  surge_model.pkl

/backend
  /data_pipeline           # Module A scripts
  /simulation               # Module B: flood, routing, allocation
  /ai                        # Module D: Gemini schema + prompt
  main.py                    # Module C: FastAPI app

/mobile                     # Module E: Expo app

AGENTS.md, CLAUDE.md, GEMINI.md   # identical content, tool-specific filenames
MEMORY.md                          # living session state + bootstrap prompt
PRD.md, Architecture.md, Rules.md, Task.md   # this document set
```

## API contract

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/surge-zone?category={0-6}` | IMD category index | GeoJSON flood polygon(s) — one per timestep frame |
| GET | `/exposure?category={0-6}` | IMD category index | `{ hospitals: [...], substations: [...], roads_cut_off: [...] }` |
| GET | `/routes?category={0-6}&origin={block_id}` | origin block + category | Safe route polyline + assigned shelter |
| GET | `/allocation?category={0-6}` | IMD category index | Block → shelter assignment table |
| POST | `/advisory` | Combined exposure + routes + allocation payload | `DistrictAdvisory` JSON (schema in CLAUDE.md) |

*(Whether `/routes` and `/allocation` are separate calls or bundled into
`/exposure`'s response is an implementation decision for whoever builds
Module C — note the choice in MEMORY.md once made, so it isn't re-decided
differently by a later session.)*

## Request sequence for one user interaction

1. User moves the intensity slider to category N.
2. App calls `/surge-zone?category=N` and `/exposure?category=N`.
3. Map updates: flood polygon renders, infra markers color by exposure,
   compromised roads render red-dashed.
4. User taps "Generate Advisory."
5. App calls `/routes` and `/allocation` for the current category.
6. App POSTs the combined payload to `/advisory`.
7. The returned `DistrictAdvisory` renders in the modal; SMS draft is
   copyable via `expo-clipboard`.

## Tech stack summary

| Tool | Used for |
|---|---|
| FastAPI + Uvicorn | API server |
| rasterio, geopandas, shapely | DEM reading, polygon geometry, spatial intersections |
| scipy (`distance_transform_edt`, `linprog`) | Flood decay math, shelter allocation LP |
| scikit-learn | Surge regression, DBSCAN hotspot clustering |
| osmnx + networkx | Road graph, shortest safe path |
| google-genai SDK + gemini-3.7-flash | Advisory generation |
| pydantic | Structured Gemini output |
| Expo + react-native-maps | Mobile map UI |
| Render / Railway | Backend hosting |

Full detail and code for each of these lives in CLAUDE.md/AGENTS.md.
