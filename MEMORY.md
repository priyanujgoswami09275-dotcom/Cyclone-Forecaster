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
| A. Data pipeline & surge ML model | In progress | 5/6 deliverables done: track, hospitals, substations, roads, surge model (LOOCV MAE 2.36 m). Missing: `data/dem.tif` — see blockers. |
| B. Simulation engine (flood propagation, routing, shelter allocation) | Not started | Next — needs `data/dem.tif` first |
| C. Backend / API (FastAPI) | Not started | |
| D. AI advisory layer (Gemini) | Not started | |
| E. Mobile app (Expo / React Native) | Not started | |
| F. Deployment | Not started | |

*(Status values: Not started / In progress / Blocked / Done)*

## What actually exists in the repo right now

- `data/remal_track.geojson` — real IBTrACS v04r00 track: 19 fixes, 2024-05-25 12Z → 2024-05-27 18Z, max USA_WIND 54 kt (JTWC 1-min; properties carry `wind_units: knots`)
- `data/hospitals.geojson` — 560 real OSM features (amenity~hospital|clinic)
- `data/substations.geojson` — 103 real OSM features (power~substation|plant)
- `data/roads.geojson` — 3712 real OSM ways (arterials)
- `data/surge_model.pkl` — joblib dict {model, features, loo_mae}; LOOCV MAE = 2.36 m
- `backend/data_pipeline/` — fetch_ibtracs.py, fetch_osm_infra.py, fetch_dem.py (works, needs GEE auth), train_surge_model.py
- `tests/` — 9 passing tests (4 IBTrACS + 3 Overpass + 2 model); run `venv/bin/pytest tests/`
- `venv/` + `requirements.txt`; repo git-init'd with per-task commits; AGENTS.md & GEMINI.md symlinked to CLAUDE.md
- NOT yet: `data/dem.tif` (see blockers)

*(List real files/paths as they get created. Keep this in sync with reality
— this is what stops the next session from re-deriving something that
already exists, or trusting a file that was later deleted.)*

## Known issues / blockers

- **GEE auth (ACTIVE BLOCKER for `data/dem.tif`):** `fetch_dem.py` is
  written and runs, but `ee.Initialize()` fails — no Earth Engine
  credentials on this machine. Remediation: `venv/bin/earthengine
  authenticate` (auth with a Google account that has a registered cloud
  project — create one at https://code.earthengine.google.com/register
  if needed), then re-run `venv/bin/python backend/data_pipeline/fetch_dem.py`.
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

*(Nothing flagged. A tool session adds a line here — not a silent change —
if it disagrees with something in AGENTS.md/CLAUDE.md.)*

## Environment / credentials status

- [ ] Google Earth Engine authenticated (`ee.Authenticate()` run once)
- [ ] `GEMINI_API_KEY` set in environment
- [ ] Backend deployed (Render / Railway) — URL: _none yet_
- [ ] Expo project initialized

## Next step

Start **Module B — simulation engine**, but the first 15 minutes are
module-A cleanup: resolve the GEE blocker and produce `data/dem.tif`
(see "Known issues / blockers" — exact commands are there). Then build
under `backend/simulation/`:

1. BFS flood propagation over the DEM grid producing N time-stepped
   frames (reference code in CLAUDE.md "Reference code" section) —
   input: `data/dem.tif` + surge height from `data/surge_model.pkl`.
2. Road network graph from `data/roads.geojson` (or osmnx re-pull for
   richer topology — decision recorded in MEMORY once made).
3. Safe-route function: Dijkstra excluding edges intersecting the current
   flood frame.
4. At-risk population per block (OSM building density or WorldPop raster).
5. Curated shelter list + capacities (OSM amenity=shelter is likely
   sparse here — expect to hand-curate Multi-Purpose Cyclone Shelters).
6. Shelter allocation LP via `scipy.optimize.linprog`.

You'll need rasterio/geopandas/shapely/osmnx/scipy — add to
requirements.txt and reinstall (`venv/bin/pip install -r requirements.txt`).

---

## Session log (newest entry first)

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
