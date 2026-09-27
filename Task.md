# Task.md — Full Task Backlog

Ordered by dependency. Check items off as they're done, and keep
MEMORY.md's "Status by module" table pointed at whichever module is
currently active — this file is the full detail, MEMORY.md is the
current-state summary.

## Module A — Data pipeline & surge ML model

- [x] Fetch IBTrACS track for Cyclone Remal → `data/remal_track.geojson`
- [x] Fetch OSM hospitals/clinics for target bbox via Overpass → `data/hospitals.geojson`
- [x] Fetch OSM power substations/plants → `data/substations.geojson`
- [x] Fetch OSM arterial roads → `data/roads.geojson`
- [ ] Fetch SRTM DEM via Google Earth Engine for target bbox → `data/dem.tif`
  - Blocked on GEE auth — `backend/data_pipeline/fetch_dem.py` is written and runs, needs `earthengine authenticate` once (see MEMORY.md blockers)
- [x] Compile historical surge training table (≥4 verified real points to
      start: Remal, Helen, Lehar, Mandous)
- [x] Train surge regression model with leave-one-out cross-validation,
      serialize as `data/surge_model.pkl` (LOOCV MAE: 2.36 m)
- [ ] (Stretch) Add more historical points from the RSMC New Delhi
      bulletin archive to strengthen the model

## Module B — Simulation engine

- [ ] Implement BFS flood propagation over the DEM grid, producing N
      timestep frames (not a single static polygon)
- [ ] Build the road network graph from OSM data with `osmnx`/`networkx`
- [ ] Implement the safe-route function (Dijkstra, excluding edges that
      intersect the current flood frame)
- [ ] Estimate at-risk population per block (building density from OSM,
      or a population raster)
- [ ] Curate/verify real shelter locations + capacities for the target
      blocks (OSM `amenity=shelter` coverage may be sparse here)
- [ ] Implement shelter allocation via `scipy.optimize.linprog`
      (transportation-problem formulation)

## Module C — Backend / API

- [ ] Scaffold the FastAPI app structure per Architecture.md
- [ ] Implement `GET /surge-zone`
- [ ] Implement `GET /exposure`
- [ ] Implement `GET /routes`
- [ ] Implement `GET /allocation`
- [ ] Implement `POST /advisory` (calls Gemini)
- [ ] Decide and document whether `/routes`/`/allocation` are separate
      calls or bundled into `/exposure` — record the decision in MEMORY.md
- [ ] End-to-end local test of all endpoints via curl/Postman

## Module D — AI advisory layer

- [ ] Define the `DistrictAdvisory` pydantic schema (see CLAUDE.md for
      the reference version)
- [ ] Write the Gemini system prompt/instruction
- [ ] Wire the `google-genai` SDK call with `response_schema`
- [ ] Test advisory generation against a real exposure payload from
      Module B/C
- [ ] Confirm the SMS draft field stays under 160 characters in practice

## Module E — Mobile app (Expo / React Native)

- [ ] Scaffold the Expo app (TypeScript, managed workflow)
- [ ] Set up `react-native-maps` with a hardcoded initial region (Sagar
      Island) — no location permission
- [ ] Render the cyclone track `Polyline` and infra `Marker`s from static
      data first, before wiring live API calls
- [ ] Wire the intensity `Slider` to `/surge-zone` and `/exposure`
- [ ] Render the flood `Polygon` and exposure counts dynamically
- [ ] Style compromised-corridor roads (red dashed `Polyline`)
- [ ] Build the "Generate Advisory" button and advisory `Modal`
- [ ] Add the `expo-clipboard` SMS-copy button

## Module F — Deployment & demo prep

- [ ] Deploy the backend (Render or Railway — see CLAUDE.md tradeoffs)
- [ ] Point the Expo app at the deployed backend URL
- [ ] Pre-warm the backend before any live demo if using Render
- [ ] Record a backup screen-capture video of the full demo flow
- [ ] Rehearse the 3-minute pitch narrative (real event vs. what this
      would have flagged)
