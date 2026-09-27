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
| C. Backend / API (FastAPI) | Not started | Next. Module B is ready to be wrapped. |
| D. AI advisory layer (Gemini) | Not started | |
| E. Mobile app (Expo / React Native) | In progress | Design system landed 2026-09-27: `mobile/theme.ts`, fonts installed, font gate in `App.tsx`, 5 themed primitives. No screen logic yet — see "Module E: what is themed vs. unstyled" below. |
| F. Deployment | Not started | |

*(Status values: Not started / In progress / Blocked / Done)*

## What actually exists in the repo right now

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
- `tests/` — **82 passing** (was 9): test_flood.py (21), test_dem_and_surge.py
  (30), test_module_b.py (22), plus Module A's 9. Run `venv/bin/pytest tests/`.
  No network access needed.
- `venv/` + `requirements.txt` (now includes rasterio, shapely, scipy, networkx,
  osmnx); AGENTS.md & GEMINI.md symlinked to CLAUDE.md
- `mobile/theme.ts` — the `theme` object copied verbatim from `Design .md` (colors, radius, spacing, shadow, fonts). No value modified or added.
- `mobile/App.tsx` — app root; `useFonts` gate over the four families in `theme.fonts`; renders nothing until fonts resolve
- `mobile/index.ts` — `registerRootComponent(App)`
- `mobile/components/` — `PriorityChip.tsx`, `ExposureRow.tsx`, `PrimaryButton.tsx`, `GhostButton.tsx`, `AdvisoryModal.tsx`, `mapStyles.ts` (all presentational, zero data/API)
- `mobile/package.json` — Expo SDK 57.0.25; `@expo-google-fonts/inter` 0.4.2, `@expo-google-fonts/roboto-slab` 0.4.2, `expo-font` 57.0.4, `typescript` + `@types/react` (dev)
- `mobile/app.json`, `tsconfig.json`, `babel.config.js` — minimal Expo managed scaffold
- `Design .md` — the visual system spec, in the repo root
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

1. **`Design.md` has a type scale but no sizes in its theme object.** Its
   typography table specifies heading 20–24px / body 15–16px / caption
   12–13px, but the "React Native theme object" carries font *families*
   only. The session rule was to invent nothing, so **every themed
   component renders with no `fontSize` at all** and falls back to the RN
   default. This is the one real gap in the design system and it needs a
   human decision: either add a `typography` block to `theme.ts` (and to
   `Design .md`), or confirm the table's ranges should be read as ranges
   and picked per component. Affected: every `fontSize` line in
   `mobile/App.tsx` and `mobile/components/*.tsx`, each marked with
   `// fontSize: omitted on purpose`.
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
5. **The design file is named `Design .md`** — a stray space before the
   extension, so plain `Design.md` lookups miss it. Left in place rather
   than renamed, in case something external references the name.
   `Architecture.md`'s repo-structure listing also does not mention
   `Design.md` at all; worth adding.
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

## Environment / credentials status

- [x] Google Earth Engine authenticated — done 2026-09-27, DEM fetched and committed
- [ ] `GEMINI_API_KEY` set in environment (Module D; must not be written to a tracked file)
- [ ] Backend deployed (Render / Railway) — URL: _none yet_
- [x] Expo project initialized — `mobile/`, Expo SDK 57.0.25, deps installed, `npx tsc --noEmit` clean
- [ ] Expo app run on a device/simulator — never launched; theme is typechecked only (spec §10.5)
- [ ] `expo-clipboard` installed — needed for the SMS copy button, not yet added

## Next step

Start **Module C — the FastAPI backend**. Modules A and B are done and
tested; this module is a thin, well-specified wrapper over code that
already works. `backend/main.py` plus `backend/ai/` for Module D.

**The API contract is fixed in Architecture.md** — implement it as written,
don't redesign it:

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/surge-zone?category={0-6}` | IMD category index | GeoJSON flood polygon |
| GET | `/exposure?category={0-6}` | IMD category index | `{hospitals, substations, roads_cut_off}` |
| GET | `/routes?category={0-6}&origin={block_id}` | origin + category | route polyline + shelter |
| POST | `/advisory` | combined exposure+routes+allocation | `DistrictAdvisory` JSON |

Key things to get right, all already handled inside the simulation layer —
your job is to pass them through, not re-derive them:

- **The parameter is `category` (0–6 IMD band), not a raw wind speed.**
  `backend/simulation/surge.py` has `IMD_CATEGORIES` (Depression → Super
  Cyclonic Storm, 17 → ≥120 kmph) and `predict_surge()` takes kmph. Map the
  index to the band's wind and call `predict_surge`. Note the dead zone
  ("Flagged for review" §8): categories 0–3 all yield 0 m surge at
  `approach_angle_flag=1`, so those responses are legitimately empty.
  Validate the index and return 422 on anything outside 0–6.
- **Every response must carry its own honesty metadata.** `exposure` returns
  a `definitions` block; `/surge-zone` must include `final_land_area_km2`
  (modelled), `drawn_area_km2`, and `is_estimate`; `/allocation` must include
  the `shelter_dataset_status()` block with `is_demo_data: true`. Do not
  strip these to make responses tidier.
- **Cache aggressively per category.** The flood run is ~6 s at 180 kmph and
  the road graph build is ~0.4 s (already `lru_cache`d). A 7-entry cache
  keyed on category makes the slider usable; without it every slider tick
  recomputes 6 s of raster work. `functools.lru_cache` on a
  `flood_for_category(n)` helper is enough.
- **No live network calls from any handler** (Rules.md). Everything reads
  committed files in `data/`. Overpass, IBTrACS and GEE are one-off
  pre-fetch scripts in `backend/data_pipeline/` only.
- **`/routes` needs an origin→node mapping** that doesn't exist yet. Pick
  block centroids (e.g. from the flood polygon or a fixed list of localities)
  and snap via `routing.safe_route`, which already returns an explicit
  unreachable result rather than raising. Return that `reason` to the client.
- **Undecided, and yours to decide once, then record here:** whether
  `/routes` and `/allocation` are separate endpoints or bundled into
  `/exposure`. Architecture.md's request sequence calls them separately
  (steps 5–6), so **default to separate endpoints**; bundle only if the
  slider UX turns out to need it. Either way, write the decision into this
  file so a later session doesn't re-decide it differently.

Then **Module D** (`backend/ai/`): the `DistrictAdvisory` pydantic schema
from CLAUDE.md, the Gemini system prompt, and the `google-genai` call with
`response_schema` on `gemini-3.7-flash`. `GEMINI_API_KEY` comes from the
environment only — never write it to a tracked file. Verify the
`sms_dispatch_draft` actually comes back under 160 characters rather than
trusting the prompt.

Install: `venv/bin/pip install fastapi "uvicorn[standard]" google-genai`.

Test the whole thing end-to-end with curl against a real category. Useful
reference numbers from a full run this session (wind kmph → land km²
flooded / hospitals / substations / roads cut): 120 → 0/0/0/0, 150 →
747/0/0/3, 180 → 1710/5/10/122, 220 → 2399/8/19/180. Routes go unreachable
above 180 kmph. If your numbers differ wildly, the wiring is wrong, not
the model.

**Module E aside (2026-09-27):** the design system is already in place —
`mobile/theme.ts`, fonts loaded through a `useFonts` gate, and five themed
primitives. When you get to the map screen, start from
`mobile/components/` and `mobile/components/mapStyles.ts` rather than
re-deriving styles. Two things need a human first: the missing font sizes
("Flagged for review" §1) and `expo-clipboard` not being installed yet.
The map screen can now be built against the real `/surge-zone` and
`/exposure` shapes above rather than invented ones.

---

## Session log (newest entry first)

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
