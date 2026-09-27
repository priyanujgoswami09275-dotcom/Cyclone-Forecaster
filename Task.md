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
- [x] Fetch SRTM DEM via Google Earth Engine for target bbox → `data/dem.tif`
  - Done: real GEE `USGS/SRTMGL1_003`, 50 m, EPSG:4326, 2898×3117. Provenance
    stamped into the raster's own tags by `backend/data_pipeline/tag_dem.py`
    and committed. (The design spec's Terrarium fallback is moot — GEE auth
    was obtained, so this is the better provenance.)
- [x] Compile historical surge training table (≥4 verified real points to
      start: Remal, Helen, Lehar, Mandous)
- [x] Train surge regression model with leave-one-out cross-validation,
      serialize as `data/surge_model.pkl` (LOOCV MAE: 2.36 m)
- [ ] (Stretch) Add more historical points from the RSMC New Delhi
      bulletin archive to strengthen the model

## Module B — Simulation engine

- [x] Implement BFS flood propagation over the DEM grid, producing N
      timestep frames (not a single static polygon) — `backend/simulation/flood.py`.
      Vectorised the dilation; the reference BFS's drained-frontier stall is
      fixed and covered by tests.
- [x] Build the road network graph from OSM data with `osmnx`/`networkx` —
      `backend/simulation/routing.py`. Built from the committed GeoJSON rather
      than osmnx, because osmnx queries Overpass live, which Rules.md forbids.
      Added `data/delta_roads.geojson` so Sagar Island is routable.
- [x] Implement the safe-route function (Dijkstra, excluding edges that
      intersect the current flood frame)
- [x] Estimate at-risk population per locality (building density from OSM) —
      `backend/simulation/population.py`. Labelled an estimate throughout.
- [ ] Curate/verify real shelter locations + capacities for the target
      blocks — **BLOCKED: no real data exists to curate.** OSM
      `amenity=shelter` here is 21 gazebos/bus shelters with no capacity
      tags; the official WBDMD page confirms 15 real MPCS in South 24 Parganas
      but publishes no locations or capacities. Recorded in
      `data/shelters.json`. The LP runs against clearly-labelled placeholder
      shelters (`is_demo_data: true`) rather than inventing figures. Needs a
      human with district contacts, or a World Bank/NCRMP shelter register.
- [x] Implement shelter allocation via `scipy.optimize.linprog`
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

- [x] Scaffold the Expo app (TypeScript, managed workflow) — `mobile/`,
      Expo SDK 57.0.25, typechecks clean. Also landed: `theme.ts` from
      `Design .md`, the four Google font families, the `useFonts` gate, and
      themed `PriorityChip` / `ExposureRow` / `PrimaryButton` /
      `GhostButton` / `AdvisoryModal` + map style constants
- [ ] Resolve the missing font sizes — `Design.md` gives a type scale but
      no sizes in the theme object (MEMORY.md "Flagged for review" §1)
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
