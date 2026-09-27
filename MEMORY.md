# MEMORY.md — Living State (read this every session, update it every session)

This file is the handoff baton between tools and sessions. AGENTS.md /
CLAUDE.md / GEMINI.md (same content, different filenames so different
tools auto-load it) describe the architecture and decisions — those don't
change often. This file describes *where things actually stand right now*
— it changes every session, updated by whichever tool worked last.

---

## 🔁 Bootstrap prompt — paste this first in any new tool or session

```
You're joining an in-progress hackathon project (Cyclone Impact &
Infrastructure Vulnerability Forecaster, Track 5, Google Code for
Communities Hackathon 2nd Edition). Before doing anything, read these
files in the repo root, in this order:

1. PRD.md — what we're building and why: problem, users, core loop,
   must-have vs. delighter features, and what's explicitly out of scope.
2. Architecture.md — how it's built: data flow, repo structure, and the
   API contract (endpoints, requests, responses).
3. Rules.md — guardrails: non-negotiable decisions, hard engineering
   rules (no live Overpass calls, gate Gemini behind a button, cite every
   historical number), and session discipline.
4. AGENTS.md (or CLAUDE.md / GEMINI.md — identical content) — the fuller
   reference doc, including the actual code for the surge model, flood
   propagation, routing, and shelter allocation.
5. Task.md — the full checklist backlog, module by module.
6. This file, MEMORY.md, in full — the current state: what's actually
   built right now, what's broken, what's next. Trust this over any
   assumption about progress you might otherwise make.

Treat decisions in PRD.md/Architecture.md/Rules.md/AGENTS.md as settled —
don't propose alternatives to things they document as already decided.
Work only on the item under "Next step" below, unless told otherwise.

Before ending this session — or as soon as you sense you're running low
on context window or usage quota — update this file yourself:
   - Update "Status by module"
   - Update "What actually exists in the repo right now"
   - Check off completed items in Task.md
   - Append one new entry to "Session log" (date, tool used, what you
     did, what broke, what's unresolved)
   - Rewrite "Next step" so the next session — possibly a completely
     different AI tool — knows exactly where to pick up, with no
     re-explaining needed

If you think a decision in Rules.md/AGENTS.md is wrong, don't silently
change direction — add it under "Flagged for review" below and keep
working within the existing decision until a human overrules it.

Confirm you've read all of the above and repeat back the current "Next
step" before starting any work.
```

---

## Status by module

| Module | Status | Notes |
|---|---|---|
| A. Data pipeline & surge ML model | Done | All 6 deliverables. DEM resolved (real GEE SRTM, tagged + committed). Stretch item (more RSMC points) still open. |
| B. Simulation engine (flood propagation, routing, shelter allocation) | In progress | Engine complete and tested on real data. Only the shelter *dataset* is missing — no real locations/capacities exist to use (see blockers). |
| C. Backend / API (FastAPI) | Done | `backend/main.py` + `backend/locations.py`. All contract endpoints live and curl-verified. `/advisory` returns 501 — that's Module D, next. |
| D. AI advisory layer (Gemini) | Not started | **Next.** `POST /advisory` is a 501 waiting for `backend/ai/`. See "Next step". |
| E. Mobile app (Expo / React Native) | In progress | Design system landed 2026-09-27 (`theme.ts`, fonts, font gate, 5 themed primitives); `theme.typography` added 2026-09-28. No screen logic yet — see "Module E: what is themed vs. unstyled" below. The API it will call is now real, so the map screen can be built against actual response shapes. |
| F. Deployment | Not started | |

*(Status values: Not started / In progress / Blocked / Done)*

## What actually exists in the repo right now

- `backend/main.py` — **the FastAPI app (Module C).** Implements the
  Architecture.md contract exactly: `/surge-zone`, `/exposure`, `/routes`,
  `/allocation`, `POST /advisory` (501, Module D), plus `/`, `/health`,
  `/categories`, `/localities`. Per-category `lru_cache` throughout:
  flood 7.5 s cold → **0.017 s warm**. Run:
  `venv/bin/uvicorn backend.main:app --port 8000`
- `backend/locations.py` — locality list (id / name / coords / search radius),
  the building-centroid loader with a numpy bbox pre-filter, and the
  study-area scoping. Owns the origin→coordinate mapping `/routes` needs.
- `data/places.geojson` — 2,362 real OSM place nodes (14 city, 12 town,
  47 village, 2,289 suburb). Source for locality names and coordinates.
- `data/buildings.csv.gz` — 660,893 real OSM building centroids, 4.8 MB
  gzipped. Feeds the population density estimate. Centroid-only on purpose:
  the polygons are hundreds of MB and density only needs the point.
- `backend/data_pipeline/fetch_osm_places.py`,
  `backend/data_pipeline/fetch_osm_buildings.py` — the two pre-fetch scripts
  for the above. Pre-fetch only; never called from a handler (Rules.md).
- `tests/test_module_c.py` — 37 tests on the API layer: 422 validation,
  honesty metadata present *and correct*, `/routes`↔`/allocation` agreement,
  no-network-calls-from-a-handler. **119 tests pass in total.**
- `data/dem.tif` — **real SRTM**, USGS/SRTMGL1_003 via Google Earth Engine
  (`getDownloadURL`, crs=EPSG:4326, scale=50), 2898×3117 px, ~46×50 m cells.
  Provenance stamped into the raster's own GeoTIFF tags by
  `backend/data_pipeline/tag_dem.py` (pixel values verified unchanged).
  41% of cells are at/below sea level (Bay + Sundarbans). **Committed.**
- `backend/simulation/dem.py` — DEM load/cache (`load_dem`, `lru_cache`).
  Latitude-corrected `metres_per_pixel()` — a degree of longitude is ~10%
  shorter than a degree of latitude at 22°N, so pixel size is 46.4×49.7 m, not
  square. Single source of truth for geo-math; nodata (SRTM `-32768`) handled.
- `backend/simulation/surge.py` — `predict_surge(wind, forward?, approach?)`.
  Loads `data/surge_model.pkl`, clamps to [0, 4] m, returns `SurgeResult`
  carrying `is_estimate`, `loo_mae_m` (2.36), `clamped`, and which features
  were assumed. IMD categories hardcoded (Depression…Super Cyclonic Storm).
- `backend/simulation/flood.py` — `simulate_flood_propagation()` (4-connected
  BFS, vectorised via `maximum_filter` with a cross footprint) and
  `run_flood_model(wind)` → `FloodResult` with 10 timestep frames. Emits ONE
  cumulative outline (last frame); per-step areas are real. Drops fragments
  < 0.5 km² for rendering and reports `dropped_detail_km2` +
  `final_land_area_km2` (modelled) vs `drawn_area_km2`.
- `backend/simulation/exposure.py` — `compute_exposure(flood)` → hospitals /
  substations submerged, roads cut_off. Handles Point AND LineString/Polygon
  facilities (most are LineStrings). `definitions` block ships with every
  result; `cut_off` is intersection, not connectivity.
- `backend/simulation/routing.py` — `build_road_graph()` (86k nodes from
  `roads.geojson` + `delta_roads.geojson`, 0.4 s, cached),
  `flooded_edges()`, `safe_route()` → Dijkstra avoiding flooded edges.
  Returns explicit "unreachable" (never a silent empty path).
- `backend/simulation/shelters.py` — `load_shelters()` (returns [] — see
  blockers), `demo_shelters()` (5 labelled placeholders), `shelter_dataset_status()`.
- `backend/simulation/allocation.py` — `allocate_shelters(nodes, shelters?)` →
  capacitated transportation LP via `scipy.optimize.linprog` (HI-GHS).
  Returns assignment, per-shelter loads, unmet_demand, and the demo-data
  disclosure. Detects infeasible (demand > capacity) explicitly.
- `backend/simulation/population.py` — `estimate_populations()`,
  `methodology()`. OSM building-density estimate, always `is_estimate: true`.
- `backend/data_pipeline/tag_dem.py` — stamps DEM provenance (idempotent,
  verifies values unchanged before replacing).
- `data/delta_roads.geojson` — 3048 real OSM ways for the delta (Sagar Island
  328, Gosaba 104) with tertiary/unclassified classes the arterial fetch
  excluded. Committed. Fetched via
  `venv/bin/python backend/data_pipeline/fetch_osm_infra.py delta_roads`.
- `data/shelters.json` — EMPTY shelter list + full provenance record (OSM
  check, official WBDMD reference). The emptiness is deliberate and documented.
- `data/remal_track.geojson` — real IBTrACS v04r00 track: 19 fixes, 2024-05-25 12Z → 2024-05-27 18Z, max USA_WIND 54 kt (JTWC 1-min; properties carry `wind_units: knots`)
- `data/hospitals.geojson` — 560 real OSM features (amenity~hospital|clinic)
- `data/substations.geojson` — 103 real OSM features (power~substation|plant)
- `data/roads.geojson` — 3712 real OSM ways (arterials)
- `data/surge_model.pkl` — joblib dict {model, features, loo_mae}; LOOCV MAE = 2.36 m
- `backend/data_pipeline/` — fetch_ibtracs.py, fetch_osm_infra.py (now takes dataset names as argv), fetch_dem.py, tag_dem.py, train_surge_model.py
- `tests/` — **119 passing** (was 82): test_module_c.py (37, new),
  test_flood.py (21), test_dem_and_surge.py (30), test_module_b.py (22), plus
  Module A's 9. Run `venv/bin/pytest tests/`. No network access needed.
- `venv/` + `requirements.txt` (now includes rasterio, shapely, scipy, networkx,
  osmnx, fastapi, uvicorn, httpx); AGENTS.md & GEMINI.md symlinked to CLAUDE.md
- `Design.md` — the visual system spec. **Renamed from `Design .md` on
  2026-09-28** (stray space gone, "Flagged for review" §5 resolved).
- `mobile/theme.ts` — the `theme` object copied verbatim from `Design.md`
  (colors, radius, spacing, shadow, fonts, typography). No value invented.
- `mobile/App.tsx` — app root; `useFonts` gate over the four families in `theme.fonts`; renders nothing until fonts resolve
- `mobile/index.ts` — `registerRootComponent(App)`
- `mobile/components/` — `PriorityChip.tsx`, `ExposureRow.tsx`, `PrimaryButton.tsx`, `GhostButton.tsx`, `AdvisoryModal.tsx`, `mapStyles.ts` (all presentational, zero data/API)
- `mobile/package.json` — Expo SDK 57.0.25; `@expo-google-fonts/inter` 0.4.2, `@expo-google-fonts/roboto-slab` 0.4.2, `expo-font` 57.0.4, `typescript` + `@types/react` (dev)
- `mobile/app.json`, `tsconfig.json`, `babel.config.js` — minimal Expo managed scaffold
- Typecheck: `cd mobile && npx tsc --noEmit` → clean, all 7 project files covered
*(List real files/paths as they get created. Keep this in sync with reality
— this is what stops the next session from re-deriving something that
already exists, or trusting a file that was later deleted.)*

## Module E: what is themed vs. unstyled

`/mobile` did not exist when this session started — Module E was untouched,
so there were no pre-existing components to restyle. What landed is the
design system itself.

**Themed and typechecking:**

| Design.md spec | Implementation |
|---|---|
| Map screen background | `theme.colors.background` in `App.tsx` container; the map screen itself is pending |
| Intensity slider track/thumb | **not implemented** — needs `border`/`primary`; pending the map screen (Module E) |
| Exposure list row | `ExposureRow.tsx` — `card` bg, `radius.card`, `border`, `danger`/`textMuted` dot |
| Priority chip | `PriorityChip.tsx` — `radius.chip` pill, `danger`/`dangerDark`/`caution`/`safe`, total `PriorityLevel` type |
| Flood polygon | `mapStyles.ts` → `floodPolygonStyle` (`waterFill` @ 0.5 fill, `water` stroke) |
| Compromised road | `mapStyles.ts` → `compromisedRoadStyle` (`danger`) + `compromisedRoadDashPattern` |
| Advisory modal | `AdvisoryModal.tsx` — `card` bg, `radius.card`, `theme.shadow.card` |
| "Generate Advisory" button | `PrimaryButton.tsx` — `primary` fill, `radius.button` |
| SMS copy button | `GhostButton.tsx` — `card` fill, 1px `border`, `text` label |
| Fonts | `useFonts` gate in `App.tsx`; all four families installed and loading |

**Still pending, owned by Module E (Task.md lines 59–70):** the map screen
itself — `MapView` on a hardcoded Sagar Island region, the intensity
`Slider`, flood `Polygon` render, live exposure counts, the Remal track
`Polyline`, the `/api/simulate` and `/api/advisory` wiring, and
`expo-clipboard` (not yet installed). Deliberately not built here: this
session's rule was not to write another module's logic. Also still open per
`docs/superpowers/specs/2026-09-27-core-loop-design.md` §10.5 — the app has
never been run on a device or simulator, so the theme is **typechecked but
not visually verified**.

## Known issues / blockers

- **GEE auth: RESOLVED 2026-09-27.** Credentials obtained, `data/dem.tif`
  fetched (real `USGS/SRTMGL1_003`, 50 m, EPSG:4326, 2898×3117) and committed.
  Provenance stamped into the raster's tags. No further GEE work needed.
- **Shelter locations/capacities (ACTIVE BLOCKER for Module B item 5, does
  NOT block the API):** there is no real shelter dataset to load. OSM
  `amenity=shelter` in the bbox is 21 gazebos/bus shelters with no capacity
  tags — 0 usable. The official WBDMD page confirms 15 real Multi-Purpose
  Cyclone Shelters in South 24 Parganas but publishes no coordinates and no
  capacities. The LP therefore runs against 5 placeholders carrying
  `is_demo_data: true`, and every allocation result embeds that disclosure.
  Needs a human with district contacts, or a World Bank / NCRMP shelter
  register. **Do not invent shelter data to make the demo look complete.**
  - **LEAD, don't re-search this (2026-09-28):** North 24 Parganas'
    official **District Disaster Management Plan** (PDF hosted on
    **wbxpress.com**) has an annexure literally titled *"Multipurpose
    cyclone shelter with details."* It is gated behind free site
    registration, which is why it has not been pulled. If someone
    registers (or is a district contact), that annexure is the most
    likely real source of shelter coordinates + capacities for the
    northern blocks. Note it covers North 24 Parganas, not South 24
    Parganas — the WBDMD count of 15 MPCS for our study district is a
    separate, still-unlocated source.
- **Drawn vs. modelled flood area diverge.** SRTM quantises elevation to whole
  metres, so in the 0–4 m delta a realistic surge shatters into 45k–541k
  slivers. The renderer drops bodies < 0.5 km², so `drawn_area_km2` can be
  well under `final_land_area_km2` (at 180 kmph: 1710 modelled vs 749 drawn).
  Both numbers, plus per-frame `dropped_detail_km2`, ship with every response.
  Never present `drawn_area_km2` as the flooded area. See "Flagged for
  review" §9.
- **IBTrACS URL moved:** the v04r00 path in CLAUDE.md 404s; the dataset now
  lives under `...-stewardship-ibtracs/v04r00/...` (fixed in
  fetch_ibtracs.py — same dataset, same version).
- **Overpass host:** overpass-api.de rejects this machine's IP (406 on
  every query); kumi/mail.ru time out on full-bbox queries from here.
  fetch_osm_infra.py currently uses overpass.openstreetmap.fr (works,
  ~10 s per query).
- **Surge model accuracy:** LOOCV MAE is 2.36 m — honest but large,
  because n=4 with 3 features. Adding more real RSMC New Delhi bulletin
  points (stretch item in Task.md) is the fix — do not swap the regression
  for a lookup table.

## Flagged for review

*(A tool session adds a line here — not a silent change — if it disagrees
with something in AGENTS.md/CLAUDE.md, or hits a gap in Design.md.)*

1. **RESOLVED 2026-09-28 — font sizes now exist.** A `typography` block
   (heading 22, body 16, emphasis 16, caption 13) was added to both
   `Design.md` and `mobile/theme.ts` (`theme.typography`). The tokens are
   in place; **the themed components still carry no `fontSize`**, so
   applying them across `mobile/App.tsx` and `mobile/components/*.tsx`
   (each line still marked `// fontSize: omitted on purpose`) is Module E
   work. If a size is changed, change `Design.md` and `theme.ts` together.
2. **"White text" is not a token.** `Design.md`'s chip and button specs
   call for white text, but the token table has no `white`. Mapped to
   `theme.colors.card`, which is the same `#ffffff` — no new value
   introduced, but the mapping should be confirmed.
3. **Dashed compromised roads won't render dashed on Android.**
   `react-native-maps` ignores `lineDashPattern` on the Google Maps
   renderer used on Android, so the PRD's "flooded roads shown as red
   dashed lines" delighter will show as a solid terracotta line there.
   iOS is fine. The dash/gap numbers in `mapStyles.ts` are also
   unspecified by `Design.md` — a placeholder pair, not a design decision.
4. **Pressed/disabled states have no tokens.** `Design.md` defines no
   pressed or disabled colour, so `PrimaryButton`/`GhostButton` use a flat
   `opacity` change rather than a new colour.
5. **RESOLVED 2026-09-28 — renamed `Design .md` → `Design.md`.** The stray
   space is gone. `Architecture.md`'s repo-structure listing still does not
   mention `Design.md` at all; worth adding there.
6. **`npm 12` blocks install scripts by default** (EALLOWSCRIPTS). A global
   `~/.npmrc` already sets an `allow-scripts` allowlist that does not cover
   `@expo-google-fonts/*`, so installing the font packages fails until
   `mobile/package.json` declares its own `allowScripts` field (which then
   supersedes the global list). That field is now committed in
   `mobile/package.json` — **remove it only if the global allowlist is
   updated to include the font packages**, or installs break again.
   Related: `npx expo install` failed on this even with the field present;
   a plain `npm install --save` worked. Prefer the plain form here.
7. **CLAUDE.md's published flood-propagation snippet is buggy — it
   under-floods.** The "Flood propagation (BFS cellular automaton)" reference
   in AGENTS.md/CLAUDE.md drains its frontier each step and refills it only
   from cells newly flooded *this* step. A step that floods nothing (because
   the surge level has not risen enough to cross a cell) empties the queue
   permanently, so propagation stalls. Observed: `maxcol per step:
   [4,4,4,4,4,4,4,4,4,4]` — the front never advanced past the ocean seed.
   The fix is to re-seed each step from the whole flooded set, not just the
   new frontier. The snippet is preserved verbatim in
   `tests/test_flood.py` as `literal_reference_bfs` with a docstring saying it
   under-floods; `corrected_literal_bfs` is the oracle, and
   `test_reference_snippet_is_known_to_under_flood` pins the difference.
   **The reference doc has not been edited** — flagging rather than silently
   diverging. A human should decide whether to fix CLAUDE.md.
8. **The surge model has a dead zone below ~115 kmph.** With
   `approach_angle_flag=1` (the case-study-accurate choice, kept deliberately),
   the fitted regression returns ≤ 0 m for everything under roughly 115 kmph,
   which `predict_surge` clamps to 0. Practically: the intensity slider does
   nothing from 0–115 kmph and only starts moving above it. This is honest
   given n=4, and the user chose to keep it rather than tune the model to
   look responsive. If it reads as "the app is broken" in a demo, the fix is
   more training points (the Task.md stretch item), not a fudged fit.
9. **`drawn_area_km2` is much smaller than `final_land_area_km2` at higher
   surges, by design.** Whole-metre SRTM quantisation fragments the 0–4 m
   delta; bodies under 0.5 km² are dropped for rendering. Both figures and the
   dropped area travel with every response, but any UI copy or Gemini prompt
   must use the *modelled* number and say it is modelled. Alternative
   approaches (morphological closing, coarser resampling, percentile
   thresholds) were tested and rejected: closing destroyed ~200 km² of real
   extent, and resampling would misreport the modelled area.
10. **The core-loop design spec conflicts with MEMORY.md, and MEMORY.md
   won.** `docs/superpowers/specs/2026-09-27-core-loop-design.md` (approved in
   an earlier chat) scopes a thin vertical slice that would have skipped most
   of Module B. The user was shown both and chose **Module B in full**. The
   spec's decision D2 (Terrarium DEM as the elevation source) is moot — real
   GEE SRTM was obtained instead, which is better provenance. Don't
   re-litigate; the spec remains useful for its API-shape ideas only.
11. **osmnx is in `requirements.txt` but deliberately unused.** CLAUDE.md
   lists osmnx for the road graph; `routing.py` builds the graph from the
   committed GeoJSON with networkx instead, because osmnx queries Overpass
   live, which Rules.md forbids. Left installed because CLAUDE.md specifies it
   and other tools may reach for it — but nothing should import it.
12. **The study area is cut at latitude 22.40°N, and that cut is an
    approximation, not a boundary.** The DEM bbox clips northern Kolkata,
    whose 12 km-radius density boxes would have produced ~90% of the raw
    demand estimate and swamped the delta. `backend/locations.py` therefore
    exposes 46 of the 77 fetched localities as the study area and the rest
    via `/localities?scope=all`. `scoping()` names every excluded place and
    says in the payload that this "is not the boundary." It is a scoping
    decision to keep the demo legible — if a user asks why Kolkata is
    missing, that is the honest answer, not a data gap.
13. **Demo shelter capacities are derived from demand, not surveyed.** With
    real shelters unavailable, `shelters_for_category()` scales placeholder
    capacities to `ceil(demand × 1.10)`, so the LP is feasible and the
    capacity/distance trade-off genuinely binds (at category 6, 4 of 5
    shelters sit at 100% occupancy). `/allocation.capacity_basis.rule`
    states this in the words "DERIVED" and "NOT surveyed", and `/health`
    reports `shelters_verified: 0`. **Any UI copy or Gemini prompt must
    carry that disclosure through** — a reader who sees "4 of 5 shelters
    full" must not read it as a real facility count. This scales demo data
    to make a feature demonstrable; it is not evidence about real shelters.
14. **The committed OSM road extract does not connect the delta to the
    inland towns.** `/routes?category=0&origin=canning` is unreachable at
    *zero* surge, and the naive reason ("origin cut off") blamed a flood
    that does not exist — the dangerous direction to be wrong in, since
    "cut off" reads as a warning. `main._diagnose_unreachable()` now
    compares `networkx.connected_components` labels and distinguishes
    road-data coverage from flood severance. **The underlying gap is still
    there**: Canning and other inland origins are unroutable at every
    category, because the committed extract has no connecting edges.
    Fixing it means a wider `data/delta_roads.geojson` fetch, not a code
    change.
15. **`httpx` warns that `starlette.testclient` should move to `httpx2`.**
    Harmless for the 119-test suite today. If a future Starlette/FastAPI
    release hard-fails on it, `tests/test_module_c.py` is the only file
    affected — the app itself never uses httpx, which is a
    `fastapi.testclient` dependency only and is commented as such in
    `requirements.txt`.

## Environment / credentials status

- [x] Google Earth Engine authenticated — done 2026-09-27, DEM fetched and committed
- [x] FastAPI backend runs locally — `venv/bin/uvicorn backend.main:app --reload`,
      verified with curl on 2026-09-28. `httpx` installed (TestClient only).
- [ ] `GEMINI_API_KEY` set in environment (Module D; must not be written to a tracked file)
- [ ] Backend deployed (Render / Railway) — URL: _none yet_
- [x] Expo project initialized — `mobile/`, Expo SDK 57.0.25, deps installed, `npx tsc --noEmit` clean
- [ ] Expo app run on a device/simulator — never launched; theme is typechecked only (spec §10.5)
- [ ] `expo-clipboard` installed — needed for the SMS copy button, not yet added

## Next step

Start **Module D — the AI advisory layer**, in `backend/ai/`. Module C is
done and tested, so this is a genuinely new piece of work rather than more
wiring: the only untested thing left in the system is text that a machine
writes.

Install first: `venv/bin/pip install google-genai`.

**The contract is already waiting.** `POST /advisory` currently returns
**501 "Module D is not implemented yet"** — deliberately a hard 501 rather
than a stub, so the client can detect the gap instead of rendering empty
or invented copy. Replace that handler; do not paper over it.

The payload it must accept is exactly what the endpoints already return:
`/surge-zone`, `/exposure`, `/routes`, `/allocation` for one category and
one origin. `/allocation` carries `capacity_basis`, `population_method`,
`shelter_status.is_demo_data`, and `/surge-zone` carries `area_disclosure`
and `is_estimate` — **feed those disclosure fields into the prompt or the
output will overclaim.** Gemini must not be told it is describing real
facility loads when `shelters_are_real` is `false` (see "Flagged for
review" §13), and must not present `drawn_area_km2` as the flooded area
(§9). The historical comparison must be traceable to a real named cyclone,
never invented (Rules.md).

What to build, per CLAUDE.md:

- `DistrictAdvisory` / `EvacuationPriority` pydantic schemas, verbatim
  from the reference code in CLAUDE.md.
- The Gemini system prompt / instruction.
- The `google-genai` call with `response_schema` against
  `gemini-3.7-flash`.
- Tests in `tests/test_module_d.py` that assert the honesty rules, not
  just the schema shape: `sms_dispatch_draft` actually under 160
  characters (measure it, don't trust the prompt), no `CRITICAL` priority
  for a block that is not in the exposure payload, and the shelter
  disclaimer present in the output.

`GEMINI_API_KEY` comes from the environment only — never write it to a
tracked file. Add a test-skip guard so the suite still runs for anyone
without a key, and note the variable in the checklist above.

**Module E aside:** the design system is in place — `mobile/theme.ts`,
fonts loaded through a `useFonts` gate, five themed primitives. Start the
map screen from `mobile/components/` and `mobile/components/mapStyles.ts`
rather than re-deriving styles, and build it against the **real** response
shapes documented above. `theme.typography` now exists but no component
applies it yet ("Flagged for review" §1) — that is Module E's first job.
`expo-clipboard` is still not installed; it is needed for the SMS copy
button.

---

## Session log (newest entry first)

### 2026-09-28 — Claude Code (Opus 5): Module C — FastAPI backend

- **Scope:** `backend/main.py` + `backend/ai/` groundwork. The brief's contract
  table was followed as written; no endpoint was redesigned, and no tested
  simulation logic was re-derived. `POST /advisory` is a hard 501 — Module D
  is next, and a stub would have been worse than a gap.
- **Also landed first (housekeeping, not this session's work):** `Design .md`
  → `Design.md`; the `typography` block added to `Design.md` and copied into
  `mobile/theme.ts`. The updated `theme.ts` was sitting at the **repo root**
  as a stray file, not at the path `mobile/App.tsx` imports — so the app was
  still importing the version without font sizes. Both files are now correct.
  No further action taken on either, as instructed.

**Built**

- `backend/main.py` — the full contract. `GET /surge-zone`, `/exposure`,
  `/routes`, `/allocation` all live, plus `/categories`, `/localities` and
  `/health` for the slider and origin picker. `category` is an IMD band
  index 0–6, mapped to a wind via `IMD_CATEGORIES` then handed to
  `predict_surge()`; `Query(ge=0, le=6)` makes out-of-range a 422 with no
  hand-rolled validation.
- `backend/locations.py` — the origin→node mapping `/routes` needed and that
  did not exist: 77 localities from OSM, 46 in the study area, with
  `scoping()` disclosing every excluded name.
- `data/places.geojson` (2,362 place nodes) and `data/buildings.csv.gz`
  (660,893 building centroids), pre-fetched with two new
  `backend/data_pipeline/` scripts. Chosen as centroids over polygons: the
  same count in 4.8 MB instead of ~85 MB, leaving `population.py`'s tested
  `buildings: list[(lon, lat)]` interface untouched.
- `tests/test_module_c.py` — 37 tests. Suite is now **119 passing** (was 82),
  no regressions in Modules A or B.

**Decisions made once, as instructed**

- `/routes` and `/allocation` are **separate endpoints**, matching
  Architecture.md's request sequence. No concrete reason for the slider to
  need bundling came up. They are now pinned against each other by
  `test_routes_and_allocation_agree_on_shelters`, because they are separate
  code paths over the same shelter set and *did* drift once (below).
- The study-area latitude cut and the demand-derived shelter capacities were
  both put to the user and chosen deliberately, not defaulted into. Both are
  recorded in "Flagged for review" §12–13 with the reasoning, because both
  scale demo data in ways a viewer could misread as findings.

**Verified**

- Every endpoint curl-tested against a real category. Category 6 (185 kmph)
  → 1,820.83 km² modelled / 833.5 drawn, 6 hospitals, 10 substations, 124
  roads cut off. Consistent with MEMORY.md's 180 kmph reference
  (1,710 / 5 / 10 / 122); the wiring is right.
- Caching works end to end: `/surge-zone?category=6` **7.55 s cold → 0.017 s
  warm**. `/allocation` at category 6 completes in ~1.5 s.
- `test_no_handler_calls_a_network_service` monkeypatches
  `requests.get/post/Session.request` to raise and then exercises the
  handlers, so the Rules.md no-live-network rule is now enforced by the
  suite rather than by convention.

**Broke, and was fixed**

- `pkill` was denied by the permission classifier ("Interfere With
  Workloads" — it could kill pre-existing processes). Used
  `lsof -ti :8000 | head -1 | xargs -I{} kill {}` to target the exact PID.
- `/allocation` kept reporting 5,100 capacity after the shelter fix, because
  the handler called `allocate_shelters(populations)` with the *default*
  shelters while `capacity_basis` used the scaled set. Exactly the
  `/routes`↔`/allocation` drift decision (1) was meant to prevent. Now
  pinned by a test.
- `/routes?category=0&origin=canning` returned "no flood-free route: origin
  cut off" at **zero surge**, blaming a flood that does not exist — the
  misleading direction, since "cut off" reads as a warning. Added
  `_diagnose_unreachable()`, which compares connected-component labels and
  separates road-data coverage from flood severance.
- Four pytest 10 deprecation warnings (class-scoped fixtures defined as
  instance methods) — fixed with `@classmethod`.
- The brief said "categories 0–3 legitimately return 0 m surge". The actual
  band mapping gives **0–4 at 0 m and category 5 at 0.06 m**. Reported rather
  than followed; the dead zone is documented in `category_band()` and
  surfaced in `/categories` instead of hidden.

**Left unresolved, deliberately**

- The road extract still does not connect the delta to Canning and other
  inland towns, so those origins are unroutable at every category. The
  message is now truthful about *why*, but the gap is a data gap — it needs
  a wider `data/delta_roads.geojson` fetch ("Flagged for review" §14).
- `httpx2` deprecation warning from `starlette.testclient` (§15).
- Real shelter data: still blocked, now with a concrete lead recorded
  (North 24 Parganas DDMP annexure on wbxpress.com, behind free
  registration) so the next session does not re-search it.
- Themed components still apply no `fontSize` (§1) — Module E.

### 2026-09-27 — Claude Code (Opus 5): Modules A closeout + Module B simulation engine
- Scope: everything under the previous "Next step" — resolve the DEM blocker,
  then build the full Module B simulation engine.
- **Starting surprise:** an approved design spec
  (`docs/superpowers/specs/2026-09-27-core-loop-design.md`) proposed a thin
  vertical slice that contradicted MEMORY.md's Module B. Shown to the user,
  who chose **MEMORY.md — Module B in full**. Logged as "Flagged for
  review" §10 so it isn't re-litigated.
- Did: obtained GEE credentials and fetched real `USGS/SRTMGL1_003`
  (50 m, EPSG:4326, 2898×3117) → `data/dem.tif`, committed, provenance
  stamped into the raster's own GeoTIFF tags by
  `backend/data_pipeline/tag_dem.py` (idempotent, pixel values verified
  unchanged). **Module A is now complete.**
- Did: built `backend/simulation/` — `dem.py` (geo-math, latitude-corrected
  metres-per-pixel), `surge.py` (predict + IMD bands + clamping),
  `flood.py` (time-stepped propagation), `exposure.py`, `routing.py`
  (Dijkstra over a networkx graph), `shelters.py`, `allocation.py`
  (HI-GHS transportation LP), `population.py`. Widened the Overpass pull to
  add `data/delta_roads.geojson` (3048 ways) so Sagar Island — the case
  study's actual landfall — is routable at all. Tests: **82 passing, up
  from 9**. Committed as `f5cad15`.
- Broke/worked around:
  - **CLAUDE.md's reference BFS under-floods.** It drains its frontier each
    step and refills only from newly-flooded cells, so any step that floods
    nothing kills propagation permanently. Fixed in `flood.py` by re-seeding
    from the whole flooded set; the original snippet is kept in
    `tests/test_flood.py` as an oracle pair. "Flagged for review" §7.
  - **Vectorised flood initially leaked diagonally** — `maximum_filter`
    defaults to an 8-connected `size=3` window. Fixed with an explicit
    cross `footprint`; now matches the corrected literal BFS exactly on 15
    random cases.
  - **Runtime 108 s → 6 s.** Profiling showed polygonisation, not BFS, was
    the cost (80 s). Vectorised the dilation and emit one cumulative outline
    instead of 10 nested full-water frames.
  - **MultiPolygon `is_valid=False`** — parts from 4-connectivity on a
    diagonally-touching mask self-intersect. Fixed with `unary_union` before
    `simplify`.
  - **100/560 hospitals and 101/103 substations are LineStrings, not
    Points.** A Point-only exposure check silently dropped them while still
    returning a plausible count. Now parses all geometry types.
  - **Two of my own test fixtures were wrong**, not the code: an "enclosed"
    test pit was actually adjacent to the ocean strip, and a 5-step
    propagation can't cross a 6×6 grid. Both fixed; worth remembering when
    a test disagrees with the implementation.
  - SRTM's whole-metre quantisation shatters the 0–4 m delta into up to
    541k slivers. Closing and resampling were both tested and rejected as
    destructive; a 0.5 km² filter plus explicit area reporting was chosen
    instead. "Flagged for review" §9.
- **Refused to fabricate:** no real shelter dataset exists (OSM gives 21
  gazebos, 0 usable; WBDMD confirms 15 real MPCS but publishes no locations
  or capacities). Recorded the full search in `data/shelters.json`, ran the
  LP against labelled placeholders, and made every allocation response say
  `is_demo_data: true`. This is the one open Module B item.
- Verified end-to-end: 120/150/180/220 kmph → 0/747/1710/2399 km² land
  flooded, 0/0/5/8 hospitals, 0/0/10/19 substations, 0/3/122/180 roads cut;
  routes unreachable above 180 kmph.
- Open for a human: the shelter dataset (needs district contacts or a
  World Bank/NCRMP register), and the four "Flagged for review" items
  §7–§11.
- Next: **Module C — FastAPI backend** (see "Next step").

### 2026-09-27 — Space Bunny Free (OpenCode): Design.md integration
- Scope: land `Design .md`'s visual system into `/mobile` (Module E).
- **Starting surprise:** `/mobile` did not exist. Module E was untouched
  (every Task.md item unchecked), so "apply the theme to every component
  that already exists" had an empty premise. Design file was also named
  `Design .md`, with a space.
- Did: `mobile/theme.ts` (theme object verbatim, nothing modified);
  Expo SDK 57 scaffold (`package.json`, `app.json`, `tsconfig.json`,
  `babel.config.js`, `index.ts`); installed
  `@expo-google-fonts/inter` + `@expo-google-fonts/roboto-slab` +
  `expo-font`; `useFonts` gate in `App.tsx` with loading + error states;
  5 themed presentational primitives + map style constants.
  `npx tsc --noEmit` clean across all 7 project files.
- Did **not** do (Module E / B / C scope, per the session's own rule not to
  build another module's logic): map screen, `MapView`, slider, flood
  `Polygon` render, track `Polyline`, `/api/*` wiring, `expo-clipboard`.
  Listed in "Module E: what is themed vs. unstyled" above.
- Broke/worked around: `npx expo install` failed with `EALLOWSCRIPTS` —
  npm 12 blocks install scripts and this machine's global `~/.npmrc`
  allowlist omits `@expo-google-fonts/*`. Fixed by adding an
  `allowScripts` field to `mobile/package.json`; a plain
  `npm install --save` then worked where `npx expo install` still did not.
  Logged in "Flagged for review" §6.
- Open for a human: **the type scale has no sizes** — every themed
  component currently renders with no `fontSize` ("Flagged for review" §1).
  This is the one thing blocking the design system from being visually
  complete.
- Plan: docs/superpowers/plans/2026-09-27-design-system-integration.md
- Next: unchanged — Module B is still the critical path (see "Next step").

### 2026-09-27 — Z.Code (OpenCode, inline plan execution)
- Built Module A end-to-end: repo scaffolding (git init, venv,
  requirements.txt, AGENTS/GEMINI symlinks), IBTrACS fetch (Remal 2024
  track, 19 fixes), OSM infra fetch (560 hospitals, 103 substations,
  3712 roads), surge model trained (LinearRegression, LOOCV MAE 2.36 m,
  serialized to data/surge_model.pkl), 9 pytest tests all passing.
- Broke/worked around: IBTrACS URL 404 (dataset moved to
  `...-ibtracs` base path — updated script); Overpass overpass-api.de
  406 IP-block + kumi 504s (moved to overpass.openstreetmap.fr);
  GEE auth absent — `data/dem.tif` NOT produced (blocker above).
- Plan + ledger: docs/superpowers/plans/2026-09-27-module-a-data-pipeline.md
- Next: Module B (see "Next step" above).
