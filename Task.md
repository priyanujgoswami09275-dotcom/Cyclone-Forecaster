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
      start: Remal, Helen, Lehar, Mandous) — **superseded**. The resulting
      regression was abandoned 2026-09-28; see
      `backend/experiments/surge_regression/`. The shipped surge model is the
      anchored quadratic scaling in `backend/simulation/surge.py`.
- [x] Train surge regression model with leave-one-out cross-validation,
      serialize as `data/surge_model.pkl` (LOOCV MAE: 2.36 m) —
      **superseded / abandoned**, kept only as a record; **do not revive**.
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
      pinned `gemini-3.8-flash` string (replaced `gemini-3.7-flash` after a
      live 503 capacity block on 2026-09-28).
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
- [x] Set up the native map with a hardcoded initial region (Sagar
      Island) — no location permission. Started with `react-native-maps`;
      replaced by `LeafletMap` (Leaflet in a `react-native-webview`) in
      `2ea5037` because the former rendered black on Android.
- [x] Render the cyclone track `Polyline` and infra `Marker`s from static
      data first, before wiring live API calls. Done the other way round: the
      track comes from `GET /track` (Stage A), not from a static file, because
      a couple of the fixes have a blank USA_WIND that the fetch script writes
      as `0.0` and only the endpoint can report as *not reported*
      (**2 of 40** fixes; the earlier "five of the nineteen" predated the
      catalogue-derived track).
- [x] Wire the intensity control to `/surge-zone` and `/exposure`. **Partly
      — deliberately.** The chips drive `/exposure`; the flood is drawn from
      a pre-rendered raster `<Overlay>`, because `/surge-zone` at category 6
      returns 7.0 MB raw of geometry that the map stutters on. The
      picture is a shortcut, the numbers are not.
- [x] Render the flood `Polygon` and exposure counts dynamically
- [x] Style compromised-corridor roads (red dashed `Polyline`)
- [x] Build the "Generate Advisory" button and advisory `Modal` — `cf25a81`.
      One failure state per `ApiErrorKind`, keyed `${category}:${origin}` so an
      advisory for a moved slider or origin is flagged stale. The button is
      disabled on the `/exposure` count alone; no second gate on `/allocation`.
- [x] Add the `expo-clipboard` SMS-copy button — copies the draft string and
      nothing else, with a live `n/160` count.

- [ ] **Run the NATIVE app on a device.** Verified by `tsc --noEmit`,
      `node --test` and `pytest`, none of which render a pixel. The core loop is
      complete and unexercised on hardware (MEMORY.md §33). **The Web build is
      no longer in this category** — it was driven in a real browser against
      production on 2026-10-01 (four chips, real exposure changes, origin search,
      map layers, advisory loading and error states, four viewport widths, zero
      console errors).

## Module F — Deployment & demo prep

- [x] **Prepared and deployed** the backend for Vercel Hobby — split runtime
      requirements, root `app.py` entrypoint, `.python-version`, `vercel.json`
      excludes; measured ~298 MB against the 500 MB limit; live at
      `cyclone-forecaster-chi.vercel.app`. `render.yaml` still exists and still
      works.
- [x] **Rebuild and deploy the Web experience** (`b8090a0`) — the Web app was a
      generic dashboard with a Google Maps iframe and a stringified advisory.
      It is now the judge-facing product: a DEM-derived SVG map in place of the
      iframe, the four `strengthChips.ts` chips wired to the real API, all 45
      localities searchable, and `DistrictAdvisory` rendered as a document.
      Platform-specific, and the native map module is verifiably absent from the
      bundle three ways. Live at `cyclone-forecaster-ui.vercel.app`.
- [x] **Generate the basemap the Web map needs** — no mapping library, no
      Google key for the browser, so `backend/tools/render_basemap.py` renders
      the 0 m contour of the committed DEM to a 48 KB PNG. Deterministic,
      display-only, verified by Pillow and by re-deriving it from the raster.
- [x] **Deploy the backend** — live at `https://cyclone-forecaster-chi.vercel.app`
      (Vercel Hobby, 2 GB). Project `cyclone-forecaster`, deployment
      `dpl_DjhJU7bk3MbgvGnoWGdBE44xR5vm`. All eight endpoints verified 200
      against production 2026-10-01.
- [x] **Deploy the judge-facing Web app** — live at
      `https://cyclone-forecaster-ui.vercel.app`. Project
      `cyclone-forecaster-ui`, deployment `dpl_84AgfTBUMAuokBP1RdX9TjJExVkd`.
      Rebuilt as the real product in `b8090a0` and verified in a real browser.
      `EXPO_PUBLIC_API_URL` set on that project only. **No build command** — it
      consumes a hand-uploaded prebuilt `mobile/dist` (MEMORY.md §46).
- [ ] Point the **native** Expo app at the deployed backend URL
      (`EXPO_PUBLIC_API_URL`, then restart Expo with `-c` — the URL is compiled
      in). The Web app is already pointed; the native app still needs this.
- [ ] Pre-warm the backend before any live demo — **measured 113.9 s cold** for
      `/exposure?category=6` on 2026-10-01 (1.4 s warm), against a 300 s hard
      duration ceiling on Hobby. The client's read timeout was raised to 150 s
      to match. Hit `/exposure?category=6` first to warm the function.
- [ ] Record a backup screen-capture video of the full demo flow
- [ ] Rehearse the 3-minute pitch narrative (real event vs. what this
      would have flagged)

## Dynamic Cyclone System — COMPLETE 2026-10-02

Tracked in `docs/superpowers/plans/2026-10-01-dynamic-cyclone-system.md`
(11 tasks). All eleven landed; this file used to be the only status source, so
the outcomes are recorded here.

- [x] Task 1 — `backend/cyclones/` normalized `Cyclone`/`Waypoint` record, one
      RFC 3339 timestamp spelling (`78b343d`, `cc369b2`, `0cdaebf`, `0f576d2`)
- [x] Task 2 — IBTrACS ingestion filtered strictly `BASIN == 'NI'`
      (`ec72646`, `bb86d71`, `191019a`, `f9a261a`, `0c80700`, `3de8e87`),
      `LANDFALL` documented as a *kilometre* distance (not a flag)
- [x] Task 3 — real ATCF live provider, 4 configurable endpoints, honest
      `live_unavailable` (`d433ec6`), with `backend/cyclones/live.py` plus
      figure-guard commits (`f9a261a`, `0c80700`, `3de8e87`, `191019a`)
- [x] Task 4 — Open-Meteo supplementary weather layer, documented, no route
      (`4d96037`)
- [x] Task 5 — cache isolation on `(cyclone_id, scenario_id)` (`7ea54da`)
- [x] Task 6 — storm-peak-intensity ML; **gate FAILED** (LOOCV MAE 21.94 kt vs
      21.03 kt flat median, R² 0.075, n=300), so the median ships labelled
      `median_baseline` (`71634ab`)
- [x] Task 7 — `/cyclones`, `/cyclones/{id}/track`, `/scenarios`,
      `/live-cyclone`, `/comparison` (`b853406`); canon track from the same
      parse (`e404e80` — `/track` takes no params)
- [x] Task 8 — `POST /risk-analyst` Gemini layer on one shared quota ladder
      (`3dcb661`, plus the `e9b02a8` gzip-race fix)
- [x] Task 9 — `cycloneModel.ts`, `apiCyclones.ts`, `api.ts` scoped getters,
      tests (`e7139d4`)
- [x] Task 10 — `CyclonePicker` / `ScenarioComparePanel` / `RiskAnalystPanel`,
      masthead follows the selection (`91783dc`)
- Defects found by verification, each its own commit: `/track?cyclone_id=`
  silently returning Remal (`db9f963`); two catalogue tests racing the input
  CSV's mtime (`0b7d79e`)
- Task 11 — documentation synchronization (this file, Architecture.md, PRD.md,
  README/CLAUDE/Design/Rules/MEMORY) and final verification — **618 pytest +
  328 mobile tests**, all green

### Still NOT done (deliberate, human decisions)

- The `DIST2LAND`-in-km unit defect in the ML features is measured and
  documented (MEMORY.md §54) but **not fixed** — it requires the human's call
  because correcting it moves the gate number.
- The live ATCF feed has never returned a usable North Indian Ocean storm, so
  real live mode is unproven.
- The native app has never run on a physical phone.
- The deployed backend (`cyclone-forecaster-chi.vercel.app`) predates Tasks
  7–8 and must be redeployed before the production bundle works.
