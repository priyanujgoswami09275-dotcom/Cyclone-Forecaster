# Cyclone Forecaster

Given a cyclone's strength, show exactly which hospitals, substations and roads
go underwater across the Sundarbans delta, and generate a ready-to-send
evacuation advisory.

The map screen has three steps. Pick a storm strength (four chips). See what it
hits (three counts, live from the model). Generate the advisory (one Gemini
call, returns block-level evacuation priorities and an SMS draft).

Above those steps sits the **storm picker**: Remal by default, or any of the
**610** North Indian Ocean cyclones in the historical catalogue. Two more
actions come off the same panel — a scenario comparison, and a risk analyst
narrative.

**Every number on screen is computed at request time from committed data.** No
lookup table, no hardcoded figures in the app. Where a figure could not be
computed, the app says so rather than substituting something plausible — that
is what `live_unavailable` and the `median_baseline` label are for.

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

### The surge model is anchored scaling, not a trained model

```
surge_m = 1.2 x (wind_kmph / 115) ** 2
```

That is the whole method (`backend/simulation/surge.py`,
`anchored_quadratic_scaling`). **It is not a trained regression and it is not a
lookup table** — it is a single published anchor point scaled by a power law
chosen because storm surge rises faster than wind.

**A regression was tried and abandoned.** `backend/experiments/surge_regression/`
holds it, together with the reason it failed: the training table had **four**
rows, `[115,16,1] -> 1.2`, `[105,13,0] -> 1.6`, `[95,20,1] -> 2.9`,
`[70,14,0] -> 0.6` — three of which have no traceable source. Leave-one-out CV
over four points did not generalise, and two of the three features
(forward speed, approach angle) are inputs the app **never has** at request
time, so two thirds of the model would have been assumed rather than measured.
It was replaced on 2026-09-28. **The experiment is kept as a record of what was
tried, not as a component of the system.**

What ships is the anchor, the scaling law, and a disclosure string carried on
every surge figure: *"Screening estimate scaled from one observed event; omits
tide, pressure, bathymetry and storm size."*

### The storm-peak-intensity model did not beat its baseline

**Stated here, next to the surge law, and not in a footnote** — because the
obvious misreading of this project is that the ML fed the surge. It does not.

A separate linear model predicts each storm's **peak `USA_WIND`** from seven
IBTrACS features (translation speed, bearing, distance to land, latitude,
longitude, month, basin distance). Measured by
`venv/bin/python backend/data_pipeline/train_storm_peak_intensity.py`:

| | |
|---|---|
| Training storms | **300** (North Indian Ocean, season ≥ 1970, `USA_WIND` present) |
| Validation | **leave-one-out, 300 folds** |
| Model MAE | **21.94 kt** |
| Flat-median baseline MAE | **21.03 kt** |
| R² | **0.075** |
| Median target | 50.0 kt |
| **Gate** | **FAILED** — 21.94 ≥ 21.03 |

So **the flat median is what ships**, labelled `estimate_source:
"median_baseline"` with `is_a_prediction: false`, and the model's own output
travels alongside as `model_kt` / `model_kt_unused`. The gate exists to stop a
worse-than-constant model being sold as a prediction, and it fired. This was
recorded, not tuned until it passed.

Two consequences, both deliberate:

- **The surge law is untouched by any of this.** It is arithmetic anchored on
  Remal. The ML figure is a separate, clearly labelled estimate that feeds
  nothing.
- **This is peak intensity, not landfall intensity.** The target is the
  storm's maximum `USA_WIND` anywhere in its track — a landfall intensity
  would need a landfall point the training table does not have.

Live output from `GET /exposure?category=N` on the committed data:

| Cat | IMD band | Wind (km/h) | Surge (m) | Flooded (km²) | Hospitals | Substations | Roads cut |
|---|---|---|---|---|---|---|---|
| 0 | Depression | 40 | 0.15 | 0.00 | 0 | 0 | 0 |
| 1 | Deep Depression | 55.5 | 0.28 | 0.00 | 0 | 0 | 0 |
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

**Nor does any request read a raw upstream input.** The 27 MB IBTrACS archive the
catalogue and the ML model were both derived from is *not* in git — it is a
local input for two offline commands (`ingest_ibtracs_ni` and
`train_storm_peak_intensity`), and what the server reads instead is the
committed artefacts they produce. That was not true of the ML model until
2026-10-02: it was refitted from the archive on every request, so `/risk-analyst`
returned a bare `500` on any clean checkout and worked only where the file
happened to be lying around. Verified by running the endpoint in a clean
`git worktree`: `500` before, `200` after.

### Weather context (Open-Meteo) — built, tested, not exposed

`backend/weather/open_meteo.py` is a complete, tested client for Open-Meteo's
forecast API, following the official docs. **It is wired to no route**: the 17
endpoints in the table below include no weather endpoint, so the claim above
still holds.

It answers *"what is the wind and pressure doing at this point right now"* and
knows nothing about cyclones. It does not track a storm and cannot reach the
surge law. Four properties are enforced rather than assumed:

- **`cell_selection=sea`**, disclosed in every response — the cell is biased to
  the sea surface, so it is a grid cell several kilometres across, not the point
  you asked about.
- **`forecast_days` is validated to 0–16, the documented range**, *before* the
  request is made. Open-Meteo returns **400** outside it; raising locally gives
  the caller this project's error rather than the provider's.
- **No `apikey` is ever sent** — the endpoint needs none, and sending an absent
  or invalid key is itself a 400. A test asserts the parameter is absent, so a
  secret cannot drift into an outbound URL.
- **A missing variable in a `200` becomes `None`, never `0`.** A gust of zero
  is a claim about the atmosphere; an omitted field says nothing. Collapsing
  them is how "no data" becomes "calm".

Two of its numbers are also not the same quantity: `wind_speed_10m` is a
10-minute mean, `wind_gusts_10m` a maximum over the preceding hour. The module
never promotes one into the other.

---

## Beyond the case study

Remal stays the default and the documented anchor. The app can also select any
other storm in the catalogue.

### The historical catalogue

`GET /cyclones` serves **610** cyclones, every one of them `BASIN == 'NI'`
(North Indian Ocean), seasons **1970–2026**, built by filtering
`ibtracs.NI.list.v04r01.csv` at ingestion. Remal (`2024145N14087`) is first and
flagged `is_case_study: true`.

Selecting a storm refetches its track from `GET /cyclones/{id}/track` and
re-scopes `/exposure`, `/routes` and `/allocation` with `cyclone_id` and
`scenario_id`.

**What changes when you switch storms, and what does not.** At a *band*
scenario (`cat4`/`cat5`/`cat6`) the exposure counts are **identical across
cyclones** — the surge comes from the band's wind, not from the storm, and the
flood model runs over the same delta terrain. What changes is the **track**.
Only the `observed` scenario is storm-specific, and **every catalogue storm with a
reported peak wind has one** — `scenarios_for()` returns `(observed,) + bands`
for any storm whose wind the source published. Measured 2026-10-08: **300 of 610**
storms offer `observed`, Remal and 1996288N09092 (120.4 kmph) among them. The
other **310 have no reported peak wind**; for those `/scenarios` lists the bands
alone and `scenario_id=observed` is
`400 unknown scenario 'observed'. Available: cat0 … cat6`. That 400 is the
no-wind case, not "any storm except Remal". Call `/scenarios?cyclone_id=…`
rather than assuming — `pickSecondScenario` does.

### The live provider

`GET /live-cyclone` probes real ATCF endpoints, configured rather than mirrored
— there is no public North Indian Ocean ATCF archive to mirror.

Four endpoints, measured on 2026-10-02:

| Status | Endpoint | Result |
|---|---|---|
| **403** | `nrlmry.navy.mil/atcf_web/docs/current_storms.txt` | blocked |
| **403** | `nrlmry.navy.mil/atcf_web/current_storms.txt` | blocked |
| **404** | `nhc.noaa.gov/data/atcf/current_storms.txt` | not there |
| **200** | `nhc.noaa.gov/CurrentStorms.json` | answers, but lists **2 Eastern Pacific** storms — Rachel and Nolo, both `ep` basin |

**`live_unavailable` is the expected default state today, not an edge case.**
Because not every source answered in ATCF, the status is `live_unavailable`
rather than `no_active_storm`, and the response carries the source, the
attempted-at timestamp, the per-endpoint statuses, and this sentence:

> *No historical or case-study cyclone is being substituted for live data.*

That is a real guarantee, not copy: when the live feed is down the `cyclone`
field is **null**. Remal is never offered in its place, and the historical list
and deterministic surge simulation are unaffected by the status.

### Scenario comparison

`GET /comparison?category=N&cyclone_ids=…&scenario_id=…` returns two scenarios
of one storm side by side, with the delta between them. The UI looks the second
scenario up from `/scenarios` rather than assuming `observed`, which is what
keeps the button working for storms that have no observed scenario.

The delta's sign convention is **right − left**, in the direction of the arrow
shown: `cat6 → cat5` reads `-1.06 m surge`.

---

## The Web build

The judge-facing version is live at
**https://cyclone-forecaster-ui.vercel.app** and talks to the backend at
`https://cyclone-forecaster-chi.vercel.app`. Same three-step loop, same four
chips, same numbers — one build for a browser instead of a handset.

It is a **separate file, deliberately**: `mobile/components/MapScreen.web.tsx`
rather than a platform branch inside `MapScreen.tsx`. The browser has no native
runtime and no mapping library is used — the Web map is hand-rolled SVG. Metro
resolves the `.web.tsx` file in preference, so the Web code never enters the
native bundle. The native app is separate: it renders `LeafletMap` —
Leaflet inside a `react-native-webview` (see `mobile/components/LeafletMap.tsx`),
replacing `react-native-maps` in `2ea5037`.

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
- **The storm-peak-intensity model did not beat its baseline.** 21.94 kt LOOCV
  MAE against a 21.03 kt flat median, R² 0.075, over 300 storms. The median
  ships, labelled as a baseline with `is_a_prediction: false`. The gate failed
  and was recorded rather than tuned until it passed. See "The surge model is
  anchored scaling" above — it is stated there because it is adjacent to the
  claim it qualifies.
- **`live_unavailable` is what the live feed returns today.** All four ATCF
  endpoints were measured on 2026-10-02: 403, 403, 404, and a 200 that lists
  only Eastern Pacific storms. No live North Indian Ocean cyclone has ever been
  observed by this app. It says so, shows no storm, and never borrows Remal to
  fill the gap.
- **The native app has been run on a physical device, and reached the error
  screen.** It is verified by `tsc`, `node --test` (372) and `pytest` (674
  passed, 3 skipped). On-device it got as far as the error screen, so the
  bundle loads and React Native boots — but a full journey through the map,
  the cyclone picker, the comparison sheet and the risk-analyst panel has **not**
  been recorded on hardware. Those remain type-checked and unit-tested, and seen
  only in a browser.
  **The Web build is verified in a real browser** against the local backend:
  22 checks covering the picker, track redraw on a storm switch, the H1
  following the selection, the comparison sheet and its method footer, the
  unavailable live state, and a clean console.
- **The advisory's success rendering has not been seen from a live call.**
  Gemini's free tier allows 20 requests/day and was exhausted while this was
  built, so the loading and error states were observed live and the success
  state is covered by tests with the model stubbed. The error states are the
  real ones: a spent quota says so and does not pretend to have an advisory. Four things in
  particular need a real phone: whether the chips and the Generate button stay
  above the fold at a 40% map floor, whether the white map controls read against
  the pale basemap, whether the chip row scrolls or clips, and whether the
  dashed storm path renders dashed. (Dashed lines render correctly in the
  current Native Leaflet map; the earlier `react-native-maps` Android
  `lineDashPattern` limitation no longer applies.)

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
`POST /advisory` and `POST /risk-analyst` works normally, and `/health` reports
`"advisory_ready": false` while both AI routes return 503. Put the key in your
local `.env` (gitignored at any depth) or in the Vercel project settings — never
in a tracked file.

`GET /live-cyclone` needs no key. It is expected to answer
`live_unavailable` — see "The live provider" above before treating that as a
local setup problem.

### App

Node 20+ (developed on v26.7.0), Expo SDK 57.

```bash
cd mobile
npm install
cp .env.example .env          # set EXPO_PUBLIC_API_URL
npx expo start
```

Then scan the QR with **Expo Go**. No custom dev client, no EAS build — the
Leaflet-in-a-WebView map runs in Expo Go directly.

Two things that will bite:

- **`EXPO_PUBLIC_API_URL` must be your Mac's LAN IP**, not `localhost`. On a
  device, localhost is the device itself, and the app reports a network error
  that looks like a dead backend.
- **Restart with `npx expo start -c`** after changing it. Expo inlines
  `EXPO_PUBLIC_*` at build time; a hot reload keeps the old value.

### Tests

```bash
venv/bin/python -m pytest -q                   # 674 passed, 3 skipped
cd mobile && node --test 'tests/*.test.mjs'    # 372 pass, 0 fail
cd mobile && npx tsc --noEmit                  # clean
```

No test calls Gemini live. The AI layer is mocked; the daily quota is spent by
hand.

Two guards are worth knowing about because they have caught real problems:

- **`tests/figure_guard.py`** — pins every published figure. A number in the
  docs that does not match the running code fails the suite, which is the only
  reason the tables above can be trusted.
- **`tests/webBundleSafety.test.mjs`** — walks the Web bundle's import graph
  (23 modules) and asserts no native runtime and no hardcoded backend origin
  enters it.

---

## API

Base URL `http://localhost:8000` (or the deployed
`https://cyclone-forecaster-chi.vercel.app`). All seventeen routes:

| Route | Returns |
|---|---|
| `GET /` | Service banner: case study, anchor surge and its source, route index |
| `GET /health` | DEM shape, graph size, `advisory_ready`, model string |
| `GET /categories` | The seven IMD bands, the Remal preset, the anchor, the limitation string |
| `GET /overlays` | Pre-rendered flood PNGs per category (display shortcut only) |
| `GET /cyclones` | **610 NI cyclones**, Remal first, 1970–2026, `is_case_study` |
| `GET /cyclones/{id}/track` | One storm's track — the case study and every other storm share a parser |
| `GET /scenarios` | The scenarios a storm actually has (`observed` only where a track is committed) |
| `GET /live-cyclone` | Live ATCF probe, or an explicit `live_unavailable` with per-endpoint statuses |
| `GET /comparison` | Two scenarios side by side, with the delta between them |
| `GET /track` | **40** IBTrACS fixes for Remal, with wind provenance |
| `GET /localities` | 45 origin candidates (32 excluded by scoping) |
| `GET /basemap/basemap.png` | **Not served** — 404 by design. The basemap ships inside the Web bundle; see "The Web build" |
| `GET /surge-zone?category=N` | Surge height, method, anchor, flood polygon |
| `GET /exposure?category=N[&cyclone_id=&scenario_id=]` | Submerged hospitals and substations, cut-off roads |
| `GET /routes?category=N&origin=<id>[&cyclone_id=&scenario_id=]` | Dijkstra route avoiding flooded edges |
| `GET /allocation?category=N[&cyclone_id=&scenario_id=]` | Shelter assignment LP, loads, unmet demand |
| `POST /advisory` | Gemini synthesis over all of the above, forced to a Pydantic schema |
| `POST /risk-analyst` | Gemini risk narrative over the same inputs; `live_unavailable` and quota failures surface as themselves |

`GET /track` deliberately takes **no parameters** — it is the case-study
endpoint, and the app routes an id to `/cyclones/{id}/track` instead. This is
not a style choice: FastAPI ignores an unrecognised query string, so
`/track?cyclone_id=…` returned `200` and Remal's track for every id. See
`mobile/tests/apiCyclones.test.mjs`.

`/overlays` is a **display shortcut**: pre-rendered RGBA rasters the `<Overlay>`
layer samples as a texture. `/exposure`, `/routes` and `/allocation` all use the
full-resolution mask and are unaffected by it.

---

## Layout

```
backend/
  main.py            FastAPI app, all seventeen routes
  locations.py       locality scoping (45 in, 32 excluded)
  ai/advisory.py     Gemini call + Pydantic response_schema
  cyclones/          catalogue, scenarios, ATCF parser, live probe, registry
  ml/                storm-peak-intensity model (gate FAILED — ships the baseline)
  weather/           Open-Meteo client, documented ranges and 400-on-invalid
  simulation/        surge, flood, exposure, routing, allocation, shelters, population, dem
  tools/
    render_overlays.py   flood PNGs per intensity, for map display
    render_basemap.py    land/water PNG from the DEM's 0 m contour, for Web
  data_pipeline/     IBTrACS, Overpass, GEE fetchers (run offline, not per-request)
data/
  cyclones/          catalogue.json (610 NI storms) + committed tracks
  ml/                storm_peak_intensity.json — the measured report, gate failed
  overlays/          flood rasters + overlays.json (display only)
  basemap/           land/water raster + basemap.json (display only, Web only)
  *.geojson *.tif    track, DEM, OSM infra, shelters
mobile/
  components/MapScreen.tsx      native screen — Leaflet map in a WebView
  components/MapScreen.web.tsx  Web screen — projected SVG, no map library
  components/CyclonePicker.tsx  storm selection + live-state banner
  components/ScenarioComparePanel.tsx  two scenarios side by side
  components/RiskAnalystPanel.tsx      POST /risk-analyst
  cycloneModel.ts     wire types + live-state, freshness and delta maths
  api.ts apiCyclones.ts          request layer; `api.ts` owns route selection
  scenarioCompare.ts  presentation strings for the comparison sheet
  legend.ts strengthChips.ts exposureTiles.ts trackFacts.ts   shared logic
  mapProjection.ts webViewModel.ts basemap.ts                  Web-only logic
tests/               23 pytest modules
mobile/tests/        16 test files
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

