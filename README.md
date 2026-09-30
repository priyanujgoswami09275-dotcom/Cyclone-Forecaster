# Cyclone Forecaster

Given a cyclone's strength, show exactly which hospitals, substations and roads
go underwater across the Sundarbans delta, and generate a ready-to-send
evacuation advisory.

The map screen has three steps. Pick a storm strength (four chips). See what it
hits (three counts, live from the model). Generate the advisory (one Gemini
call, returns block-level evacuation priorities and an SMS draft).

**Every number on screen is computed at request time from committed data.** No
lookup table, no hardcoded figures in the app.

---

## The case study: Cyclone Remal, May 2024

This is built around one real, documented event rather than a generic
simulator, because a real event is checkable.

- **Landfall** between Sagar Island (West Bengal, India) and Khepupara
  (Bangladesh), May 2024.
- **Landfall wind** 110–120 km/h gusting 135 (IMD, 3-minute mean).
- **Storm surge** ~1.0–1.5 m above astronomical tide.

The **1.2 m / 115 km/h anchor** is the midpoint of those two published ranges,
and it is the single point the whole surge model is scaled from. Every other
strength in the app is that anchor extrapolated, which is why the API ships a
`limitation` string alongside every surge figure and the app shows it.

The demo question the app answers: *here is what Remal actually did — here is
what this system would have flagged 48 hours out.*

---

## What it actually computes

`anchored_quadratic_scaling` — a trained regression anchored on Remal, not a
table lookup. Live output from `GET /exposure?category=N` on the committed data:

| Cat | IMD band | Wind (km/h) | Surge (m) | Flooded (km²) | Hospitals | Substations | Roads cut |
|---|---|---|---|---|---|---|---|
| 0 | Depression | 40 | 0.15 | 0.00 | 0 | 0 | 0 |
| 1 | Deep Depression | 56 | 0.28 | 0.00 | 0 | 0 | 0 |
| 2 | Cyclonic Storm | 75 | 0.51 | 0.00 | 0 | 0 | 0 |
| 3 | Severe Cyclonic Storm | 103 | 0.96 | 0.00 | 0 | 0 | 0 |
| 4 | Very Severe Cyclonic Storm | 142 | 1.83 | 359.23 | 0 | 0 | 0 |
| 5 | Extremely Severe Cyclonic Storm | 194 | 3.41 | 1,709.57 | 5 | 10 | 122 |
| 6 | Super Cyclonic Storm | ≥222 | 4.47 | 2,680.22 | 12 | 22 | 251 |

Categories 0–3 flood nothing and 0–4 expose no infrastructure at all, which is
why the app offers four chips rather than all seven bands. Category 6 has **no
upper bound** in IMD's scheme, so its 222 km/h is a band floor, not a
representative value — the app renders it `≥222`, and every category carries a
`wind_is_band_midpoint` flag that says which it is.

Behind those counts:

- **Flood extent** — BFS cellular automaton over a 2,898 × 3,117 SRTM grid,
  time-stepped so the model produces a sequence of frames rather than one
  polygon.
- **Exposure** — hospital and substation points tested with `contains`; building
  footprints with `intersects`, because a closed ring cannot be strictly
  contained by a polygon it merely overlaps. Roads are tested for intersection
  with the flood extent, which is **not** a network-connectivity analysis.
- **Routing** — Dijkstra over an 86,636-node road graph with flooded edges
  removed. Unreachable is a real answer: a high surge severs the delta, and the
  endpoint returns `200` with `reachable: false` and a reason rather than a 404.
- **Shelter allocation** — capacitated transportation LP solved with HiGHS,
  minimising total person-km subject to shelter capacity and full coverage.

Data is pre-fetched and committed (`data/`): IBTrACS track, OSM infrastructure,
SRTM DEM, building footprints. **Nothing is fetched live from a request
handler** — the public Overpass instance rate-limits at 2 concurrent
requests/IP and a live call during a demo fails.

---

## The Web build

The judge-facing version is live at
**https://cyclone-forecaster-ui.vercel.app** and talks to the backend at
`https://cyclone-forecaster-chi.vercel.app`. Same three-step loop, same four
chips, same numbers — one build for a browser instead of a handset.

It is a **separate file, deliberately**: `mobile/components/MapScreen.web.tsx`
rather than a platform branch inside `MapScreen.tsx`. `react-native-maps` is a
native module, and imported into a browser bundle it throws
`codegenNativeComponent is not a function` on first render — which is how the
first Web attempt produced a blank page. Metro resolves the `.web.tsx` file in
preference, so the native import never enters the browser graph. The native app
still uses `react-native-maps` and is unchanged.

There is **no mapping library**. The browser has no Google Maps key and this
project ships no tile server, so the Web map is a projected SVG over a basemap
rendered from the committed DEM — the same 0 m contour the flood model floods
from, so the coastline and the water agree by construction. Deterministic,
offline, and no figure is measured off it.

Build and deploy it:

```bash
cd mobile
EXPO_PUBLIC_API_URL=https://cyclone-forecaster-chi.vercel.app \
  npx expo export --platform web --output-dir dist
cd dist && vercel deploy --prod        # project: cyclone-forecaster-ui
```

`EXPO_PUBLIC_API_URL` is set on the Vercel project, but the project has no
build command — it consumes the uploaded `dist/`, so build locally and upload.
Note `README.md` above says the app needs a LAN IP for a device; that is true
of the **native** app only. The Web build talks to HTTPS.

## Honest limits

These are on screen, not buried here.

- **It is a screening estimate, not a forecast.** The surge model omits tide,
  atmospheric pressure, bathymetry and storm size. It is scaled from one
  observed event.
- **Shelter data is a placeholder.** No verified shelter dataset exists for
  South 24 Parganas. `data/shelters.json` ships an **empty** shelter list —
  a real result, not a stub to be filled in later — and the five shelters the
  allocation endpoint returns are named `DEMO Shelter A`…`E`, with capacities
  *derived* as 1.1 × estimated exposed population rather than surveyed. The
  endpoint reports this in `shelter_status` and `capacity_basis`; the app shows
  a disclosure line above the advisory summary.
- **Population figures are estimates**, derived from OSM building-footprint
  density at an assumed 5 persons per building. Not census figures. They must
  not be used for real resource allocation.
- **"Roads cut" means the road geometry intersects the flood extent** — not
  that the road is impassable.
- **The native app has never run on a physical device.** It is verified by
  `tsc`, `node --test` (234) and `pytest` (352 passed, 3 skipped). Rendering on
  a handset is unverified. **The Web build is verified in a real browser**
  against production: all four chips drive the API, exposure changes as the
  scenario changes, the map and track draw from real data, and there are no
  console errors.
- **The advisory's success rendering has not been seen from a live call.**
  Gemini's free tier allows 20 requests/day and was exhausted while this was
  built, so the loading and error states were observed live and the success
  state is covered by tests with the model stubbed. The error states are the
  real ones: a spent quota says so and does not pretend to have an advisory. Four things in particular need a real phone: whether the chips and
  the Generate button stay above the fold at a 40% map floor, whether the white
  map controls read against the pale basemap, whether the chip row scrolls or
  clips, and whether the dashed storm path renders dashed —
  `lineDashPattern` is **not honoured** by react-native-maps on Android, so on
  Android the track and the cut-off roads draw solid while the legend shows them
  dashed.

---

## Run it locally

### Backend

Python 3.13.7 (`.python-version`; every heavy dependency has a cp313 wheel).

```bash
python3 -m venv venv
venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # fill in GEMINI_API_KEY, see below
venv/bin/uvicorn backend.main:app --reload --port 8000
```

Check it: `curl localhost:8000/health` → `"status": "ok"`, plus the DEM shape,
road-graph size and advisory readiness.

**`GEMINI_API_KEY` is optional.** Without it every endpoint except
`POST /advisory` works normally, and `/health` reports
`"advisory_ready": false` while `/advisory` returns 503. Put the key in your
local `.env` (gitignored at any depth) or in the Render dashboard — never in a
tracked file.

### App

Node 20+ (developed on v26.7.0), Expo SDK 57.

```bash
cd mobile
npm install
cp .env.example .env          # set EXPO_PUBLIC_API_URL
npx expo start
```

Then scan the QR with **Expo Go**. No custom dev client, no EAS build —
`react-native-maps` works in Expo Go directly.

Two things that will bite:

- **`EXPO_PUBLIC_API_URL` must be your Mac's LAN IP**, not `localhost`. On a
  device, localhost is the device itself, and the app reports a network error
  that looks like a dead backend.
- **Restart with `npx expo start -c`** after changing it. Expo inlines
  `EXPO_PUBLIC_*` at build time; a hot reload keeps the old value.

### Tests

```bash
venv/bin/python -m pytest -q                   # 352 passed, 3 skipped
cd mobile && node --test 'tests/*.test.mjs'    # 234 pass
cd mobile && npx tsc --noEmit                  # clean
```

No test calls Gemini live. The AI layer is mocked; the daily quota is spent by
hand.

---

## API

Base URL `http://localhost:8000` (or the deployed
`https://cyclone-forecaster-chi.vercel.app`). All eleven routes:

| Route | Returns |
|---|---|
| `GET /` | Service banner: case study, anchor surge and its source, route index |
| `GET /health` | DEM shape, graph size, `advisory_ready`, model string |
| `GET /categories` | The seven IMD bands, the Remal preset, the anchor, the limitation string |
| `GET /overlays` | Pre-rendered flood PNGs per category (display shortcut only) |
| `GET /track` | 19 IBTrACS fixes for Remal, with wind provenance |
| `GET /localities` | 45 origin candidates (32 excluded by scoping) |
| `GET /basemap/basemap.png` | **Not served** — 404 by design. The basemap ships inside the Web bundle; see "The Web build" |
| `GET /surge-zone?category=N` | Surge height, method, anchor, flood polygon |
| `GET /exposure?category=N` | Submerged hospitals and substations, cut-off roads |
| `GET /routes?category=N&origin=<id>` | Dijkstra route avoiding flooded edges |
| `GET /allocation?category=N` | Shelter assignment LP, loads, unmet demand |
| `POST /advisory` | Gemini synthesis over all of the above, forced to a Pydantic schema |

`/overlays` is a **display shortcut**: pre-rendered RGBA rasters the `<Overlay>`
layer samples as a texture. `/exposure`, `/routes` and `/allocation` all use the
full-resolution mask and are unaffected by it.

---

## Layout

```
backend/
  main.py            FastAPI app, all eleven routes
  locations.py       locality scoping (45 in, 32 excluded)
  ai/advisory.py     Gemini call + Pydantic response_schema
  simulation/        surge, flood, exposure, routing, allocation, shelters, population, dem
  tools/
    render_overlays.py   flood PNGs per intensity, for map display
    render_basemap.py    land/water PNG from the DEM's 0 m contour, for Web
  data_pipeline/     IBTrACS, Overpass, GEE fetchers (run offline, not per-request)
data/
  overlays/          flood rasters + overlays.json (display only)
  basemap/           land/water raster + basemap.json (display only, Web only)
  *.geojson *.tif    track, DEM, OSM infra, shelters
mobile/
  components/MapScreen.tsx      native screen — react-native-maps
  components/MapScreen.web.tsx  Web screen — projected SVG, no map library
  legend.ts strengthChips.ts exposureTiles.ts trackFacts.ts   shared logic
  mapProjection.ts webViewModel.ts basemap.ts                  Web-only logic
tests/               13 pytest modules
```

---

## Docs

| File | What it holds |
|---|---|
| `PRD.md` | Product requirements and the core loop |
| `Architecture.md` | Layer-by-layer design |
| `Design.md` | Visual spec, map styling, the dark theme token table |
| `CLAUDE.md` | Project brief, module assignment, corrections found while planning |
| `Rules.md` | Non-negotiables — read before changing anything |
| `MEMORY.md` | Session log, blockers, and "Flagged for review" items awaiting a human |
| `Task.md` | Current work |

