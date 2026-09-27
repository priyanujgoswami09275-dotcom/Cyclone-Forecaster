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

- [x] Scaffold the FastAPI app structure per Architecture.md — `backend/main.py`
- [x] Implement `GET /surge-zone` — `category` 0-6, both area figures + disclosure
- [x] Implement `GET /exposure` — `hospitals` / `substations` / `roads_cut_off`
- [x] Implement `GET /routes` — `origin` from `/localities`; unreachable is a
      200 with a reason, never a 404
- [x] Implement `GET /allocation` — LP solves; capacity basis disclosed
- [x] Implement `POST /advisory` (calls Gemini) — **now built (Module D).**
      Takes `category` + `origin`; 501 replaced by the real handler.
- [x] Decide and document whether `/routes`/`/allocation` are separate
      calls or bundled into `/exposure` — **decided: separate endpoints**,
      matching Architecture.md's numbered request sequence. Recorded in
      MEMORY.md.
- [x] End-to-end local test of all endpoints via curl/Postman — all verified;
      `tests/test_module_c.py` adds 37 tests (119 total, all passing)
- Added while wiring, because the endpoints could not work without them:
  - `backend/locations.py` — locality list (id/coord/radius) and the
    building-centroid loader, plus the study-area scoping
  - `data/places.geojson` (2,362 OSM place nodes) and
    `data/buildings.csv.gz` (660,893 centroids), pre-fetched and committed
  - `GET /categories`, `GET /localities`, `GET /health` for the slider and
    the origin picker
  - Per-category `lru_cache`: flood 7.5 s cold, 0.02 s warm

## Module D — AI advisory layer

- [x] Define the `DistrictAdvisory` pydantic schema — `backend/ai/advisory.py`.
      **Field is `locality_name`, not CLAUDE.md's `block_name`**: the demand
      nodes are OSM localities, and `validate_advisory` has to match the names
      in `/allocation`. CLAUDE.md left unedited and logged in MEMORY.md
      "Flagged for review" §16.
- [x] Write the Gemini system prompt/instruction — includes a closed pool of
      three verified past cyclones, so `historical_context` cannot name an
      invented storm
- [x] Wire the `google-genai` SDK call with `response_schema` against the
      pinned `gemini-3.7-flash` string
- [x] Replace the `POST /advisory` 501 — takes `category` + `origin`, calls
      the three endpoint functions so the prose and the map share one set of
      numbers; 503 without a key, 502 on SDK failure
- [x] Run `validate_advisory` on the result, retry once with the violations as
      corrections, and withhold with a 502 if it still fails
- [x] Test against a real Module B/C payload — `tests/test_module_d.py`, 30
      tests, 28 with Gemini stubbed and 2 marked `requires_key`
- [ ] Confirm the SMS draft field stays under 160 characters **in practice** —
      the validator measures it and the stubbed tests cover the boundary, but
      no live call has been made. Needs a `GEMINI_API_KEY`.

## Module E — Mobile app (Expo / React Native)

- [x] Scaffold the Expo app (TypeScript, managed workflow) — `mobile/`,
      Expo SDK 57.0.25, typechecks clean. Also landed: `theme.ts` from
      `Design.md`, the four Google font families, the `useFonts` gate, and
      themed `PriorityChip` / `ExposureRow` / `PrimaryButton` /
      `GhostButton` / `AdvisoryModal` + map style constants
- [x] Resolve the missing font sizes — `Design.md` gives a type scale but
      no sizes in the theme object (MEMORY.md "Flagged for review" §1).
      **Resolved 2026-09-28** — `theme.typography` added (heading 22, body 16,
      emphasis 16, caption 13) in both `Design.md` and `mobile/theme.ts`, and
      `Design .md` renamed to `Design.md` (the stray space is gone). The
      themed components still carry no `fontSize`; applying them is Module E
      work, deliberately not done in the Module C session.
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
