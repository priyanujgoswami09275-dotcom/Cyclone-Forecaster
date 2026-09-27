# Design — Core Loop Slice: DEM → Flood Model → API → Advisory → Expo App

**Date:** 2026-09-27
**Status:** Approved in chat; awaiting written-spec review
**Scope:** Completes Module A's last gap (DEM) and delivers Modules B, C, D, and E
as one vertical slice that closes the PRD core loop.

---

## 1. Context

The repo has Modules A's data pipeline done except the DEM (`fetch_dem.py`
exists but requires Google Earth Engine auth, which this machine does not have).
Modules B–E do not exist: no `backend/simulation/`, no `backend/main.py`, no
`backend/ai/`, no `/mobile`.

This spec turns three requested changes into a single end-to-end slice:

1. Replace the GEE DEM fetch with an unauthenticated real-terrain source.
2. Implement the distance-decay flood model and expose it via FastAPI.
3. Build the UI that drives it.

### 1.1 Decisions taken before writing this spec

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Frontend conflict | **Expo/React Native in `/mobile`** | `Rules.md` line 8–9 forbids reintroducing Leaflet or any browser-only library. `Architecture.md` puts the UI in `/mobile`. `PRD.md` success criteria require running in Expo Go. A requested `static/index.html` + Leaflet + Tailwind page was **declined** at the user's direction. |
| D2 | DEM source | **Real Terrarium elevation tiles (AWS S3)** | Unauthenticated, reachable from this machine (verified: HTTP 206). SRTM-derived over this region. A synthetic DEM was declined — fabricated terrain would violate `Rules.md`'s traceability rule. |
| D3 | API contract | **`POST /api/simulate` + `POST /api/advisory`** | Bundles what `Architecture.md` split across `/surge-zone` and `/exposure`. `Architecture.md` line 75 explicitly invites this as an implementation decision. Deviation recorded in `MEMORY.md`. |
| D4 | Surge at runtime | **Trained regression + clamp to [0, 4] m + expose MAE** | Keeps a genuinely trained model in the loop (`Rules.md`: "real algorithms — not a lookup table dressed up as a model") while refusing to publish physically impossible output. |
| D5 | Gemini layer | **Real call, verified live, with fallback retained** | User supplied a working key. Fallback stays as demo insurance, not as the primary path. |
| D6 | Model string | **Keep `gemini-3.7-flash`** | `Rules.md` says do not silently swap model versions. `gemini-3.8-flash` exists and is newer, but switching requires an explicit human decision. Logged in `MEMORY.md`, not acted on. |

---

## 2. Data layer — the DEM

### 2.1 Approach

Rewrite `backend/data_pipeline/fetch_dem.py` to drop the `ee` import entirely and
assemble the DEM from **Terrarium** elevation tiles:

```
https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png
```

Terrarium is a public, unauthenticated AWS-hosted tile service. Over this bbox
its high-zoom tiles are SRTM-derived. Provenance is recorded in the output
file's metadata and tags — the DEM is described as **"SRTM-derived (Terrarium
blend)"** in all metadata and documentation, never as bare "SRTM", because
Terrarium blends several source datasets by latitude and zoom.

Decode formula, per the Tilezen/Terrarium spec:

```
elevation_m = (R * 256 + G + B / 256) - 32768
```

Slippy-tile index math (standard Web Mercator):

```
n   = 2 ** z
x   = floor((lon + 180) / 360 * n)
y   = floor((1 - asinh(tan(radians(lat))) / pi) / 2 * n)
```

### 2.2 Verified parameters

For the existing study bbox `[87.80, 21.30, 89.20, 22.60]` (west, south, east,
north) at **z = 12**:

| Parameter | Value |
|---|---|
| Tile range | x `3046..3062`, y `1783..1799` |
| Tile count | 289 (17 × 17) |
| Native resolution | 35.4 m/px at the bbox centre latitude |
| Mosaic size | 4352 × 4352 px |
| Uncompressed (int16) | ~38 MB |

z = 13 was considered and rejected: 1089 tiles, 17.7 m/px, oversampling the
"30 m" brief for no modelling benefit.

### 2.3 Outputs and provenance

Primary output: **`data/dem/SRTM_WestBengal.tif`**, int16, LZW-compressed, EPSG:4326,
carrying in its metadata:

- `source` = `Terrarium (Mapzen/Tilezen) elevation tiles, AWS S3 public bucket`
- `srtm_derived` = `true`
- `note` = `"Terrarium blends SRTM and other source DEMs by latitude/zoom; over this bbox the high-zoom tiles are SRTM-derived."`
- `zoom`, `tile_source_url_template`, `bbox`, `generator`, `generated_at`

A second file **`data/dem.tif`** is written at the same values so the path
`Architecture.md` documents (`data/dem.tif`) and any existing consumer keeps
working. The runtime loader prefers `data/dem/SRTM_WestBengal.tif` and falls
back to `data/dem.tif`.

### 2.4 Failure behaviour

If tiles are unreachable, the script **fails loudly and writes nothing**. There
is deliberately no synthetic fallback: a fabricated DEM would make the flood
map look plausible while resting on invented terrain, which is the exact failure
mode `Rules.md`'s traceability rule exists to prevent.

### 2.5 Two repository gotchas this fixes

- `.gitignore` un-ignores `!data/*.tif`, which **does not match
  `data/dem/*.tif`**. The new file would be silently untracked. Add
  `!data/**/*.tif`.
- The existing ignore rules were never verified against a real commit; the
  terrain layer must land in git for the no-live-fetch rule to hold.

### 2.6 Accepted cost

~38 MB of terrain is committed to the repository. Accepted: `Rules.md` requires
pre-fetched, committed data with no live fetch at request time.

---

## 3. Simulation layer — `backend/simulation/`

Four small modules, each with one job.

### 3.1 `dem.py` — loading

Loads and caches the DEM once. Exposes the elevation array, the affine
`transform`, the CRS, and metres-per-pixel. A single source of truth for
"where is the terrain and how big is a pixel", so no other module re-derives
geo-math.

### 3.2 `surge.py` — wind → surge height

Loads `data/surge_model.pkl`, a joblib dict `{model, features, loo_mae}` with
features `['wind_kmph', 'forward_speed_kmph', 'approach_angle_flag']` and
LOOCV MAE **2.36 m**.

The endpoint receives only `wind_kmph`, so the other two inputs are filled from
**documented IMD-category-typical values**, marked as assumptions in code
comments and surfaced in the response as `is_estimate: true`.

Output is **clamped to [0, 4] m**.

This clamp is load-bearing, not cosmetic. The fitted model is
`surge = -12.0 + 0.0475·wind + 0.6625·forward_speed − 2.8625·approach_flag`,
which produces physically impossible output across the slider range:

All four rows below are the actual arithmetic of the fitted model, verified
against `data/surge_model.pkl`:

| wind (kmph) | forward | approach | raw prediction | note |
|---|---|---|---|---|
| 31 | 15 | 0 | **−0.59 m** | negative — a landfall surge cannot be negative |
| 120 | 15 | 0 | +3.64 m | real Remal value was 1.2 m |
| 120 | 15 | 1 | +0.78 m | a binary flag swings the answer by 2.86 m |
| 250 | 10 | 0 | +6.50 m | far outside plausible Bay of Bengal surge |

Every `/api/simulate` response therefore carries `surge_is_estimate: true`,
`surge_model_loo_mae_m: 2.36`, and `surge_model_clamped: bool`, so no consumer
can mistake the number for an observation. This is `Rules.md`'s traceability
rule applied at the API boundary.

The 1.2 m Remal anchor remains the reference value in copy and docs; the
regression is what drives the map, and its weakness is stated wherever its
output is shown.

### 3.3 `flood.py` — `run_flood_model(dem, transform, wind_kmph)`

The distance-decay bathtub, as requested. `scipy`'s `distance_transform_edt`
is the named tool in `Architecture.md`'s stack for "flood decay math".

1. Predict surge `S` (§3.2), clamped.
2. `ocean = dem <= 0`. Then `d = distance_transform_edt(~ocean)` yields, for
   every land cell, its **inland distance to the sea in metres** (sampling
   `(dy_m, dx_m)` so the result is metric, not pixels).
3. Water head decays inland: `h(d) = S · exp(-d / L)`, `L` = decay length,
   default **3000 m**, recorded as a documented assumption.
4. A cell floods when `dem <= h(d)`.
5. **Connectivity enforcement.** Flooded cells are labelled with
   `scipy.ndimage.label`; only components touching the ocean are kept. Without
   this, inland depressions below the decayed head would appear as isolated
   lakes, which is not surge behaviour.
6. Vectorise with `rasterio.features.shapes(flooded.astype('uint8'),
   mask=flooded, transform=transform)` → exact polygons, no extra dependency.
7. Return a FeatureCollection plus inundated area in km².

Ocean cells satisfy `dem <= 0 <= h(d)` and so are included, which is what makes
the result a connected water body rather than a fringe.

`L = 3000 m` is a **tuned assumption, not a measured value.** It is a
documented constant with a named default, not a fitted parameter, and is
labelled as such wherever the model's output is described.

### 3.4 `exposure.py` — infrastructure intersection

Pure `json` + `shapely`. **No `geopandas`** — it is a heavy dependency the work
does not require, and `Architecture.md` lists tools, not mandates.

| Layer | Test | Property |
|---|---|---|
| Hospitals (560 pts) | point inside flood polygon | `submerged` |
| Substations (103 pts) | point inside flood polygon | `submerged` |
| Roads (3712 ways) | geometry intersects flood polygon | `cut_off` |

**Honest v1 limitation, stated in code and API docs:** road "cut off" here means
*intersects the flood extent*, not a Dijkstra connectivity analysis. True
network cut-detection is the routing task in `Task.md` Module B, not this slice.
The API response names the field `cut_off` and the response documents the
definition so no consumer over-reads it.

**Addition beyond the original request:** substations are included. `PRD.md`
lists infrastructure exposure as a must-have naming hospitals, substations, and
roads; the GeoJSON is already fetched; the marginal cost is small.

---

## 4. API layer — `backend/main.py`

### 4.1 `POST /api/simulate`

Request:

```json
{ "wind_kmph": 120, "forward_speed_kmph": null, "approach_angle_flag": null }
```

The two optional fields override the IMD-category-typical defaults for
experimentation; both default to `null`.

Response (values below are **illustrative of the response shape, not observed
figures** — every numeric field is a placeholder except where noted):

```json
{
  "wind_kmph": 120,
  "imd_category": "Super Cyclonic Storm",
  "surge_m": 0.78,
  "surge_is_estimate": true,
  "surge_model_loo_mae_m": 2.36,
  "surge_model_clamped": false,
  "surge_model_features": { "forward_speed_kmph": 15, "approach_angle_flag": 1 },
  "flood_area_km2": 412.6,
  "flood": { "type": "FeatureCollection", "features": [] },
  "exposure": {
    "hospitals":   { "count": 42,  "features": [] },
    "substations": { "count": 7,   "features": [] },
    "roads":       { "count": 310, "features": [] }
  },
  "definitions": {
    "hospital_submerged": "point lies inside the flood polygon",
    "road_cut_off": "road geometry intersects the flood polygon; NOT a network connectivity analysis"
  }
}
```

The example is internally consistent: 120 kmph falls in the IMD "Super Cyclonic
Storm" band (≥120), and with `forward_speed_kmph: 15` / `approach_angle_flag: 1`
the model genuinely predicts 0.775 m, shown rounded to 0.78.

The `definitions` block travels with every response so the client never has to
guess what a count means.

**IMD wind categories** are hardcoded from the IMD 3-min mean wind
classification (km/h), cited in a code comment: Depression 17–27, Deep
Depression 28–33, Cyclonic Storm 34–47, Severe Cyclonic Storm 48–63, Very
Severe Cyclonic Storm 64–89, Extremely Severe Cyclonic Storm 90–119, Super
Cyclonic Storm ≥120. The slider spans 31–250 kmph and the category label is
derived, so the app's seven states match `Architecture.md`'s `category={0-6}`.

### 4.2 `POST /api/advisory`

Request body is the `/api/simulate` response, matching `Architecture.md`'s
"client POSTs the combined payload" sequence. Returns a `DistrictAdvisory`
JSON plus:

```json
{ "source": "gemini" | "local_fallback", "disclaimer": null | "..." }
```

### 4.3 Other

- `GET /health` — readiness plus which optional data files loaded.
- CORS middleware enabled for the Expo client.
- **All data loads once at startup.** No request handler performs a network
  call, per `Rules.md`.

---

## 5. AI layer — `backend/ai/advisory.py`

`DistrictAdvisory` and `EvacuationPriority` are the pydantic schemas exactly as
`CLAUDE.md` specifies. `google-genai` with `response_schema`, model pinned to
`gemini-3.7-flash`.

**Model verified live during design.** The key authenticates, `gemini-3.7-flash`
is present in the account's model list, and a live `generateContent` call
returned HTTP 200. One earlier attempt returned `503 UNAVAILABLE — high demand`
before succeeding on three consecutive retries. That is direct evidence the
pinned model can be transiently unavailable.

Therefore the call path is: **retry with exponential backoff on 429/503 →
fall back to a locally computed advisory.** This is evidence-driven, not
speculative hardening.

**Hallucination guard.** The prompt supplies only real place names present in
the OSM data and explicitly forbids inventing administrative block names.
Without this, Gemini invents block names and the advisory carries untraceable
fabrications — a direct `Rules.md` violation. No block-level dataset exists yet
(`Task.md` lists it as open), so the advisory reasons over named localities.

**SMS length.** `sms_dispatch_draft` is validated server-side to be under 160
characters; a longer draft is truncated and the response flags it.

**Fallback.** Built deterministically from the exposure counts, tagged
`source: "local_fallback"` with a visible disclaimer explaining that Gemini was
unavailable. The demo never dead-ends.

---

## 6. Mobile layer — `/mobile`

Expo managed workflow, TypeScript. **Two screens total** — map screen plus
advisory `Modal` — per `Rules.md` scope discipline.

| Requirement | Implementation |
|---|---|
| Full-screen map on Sagar Island | `react-native-maps` `MapView`, hardcoded `initialRegion` 21.65 / 88.10. No location permission. |
| Wind slider | `@react-native-community/slider`, 31–250 kmph, live IMD category label. |
| Live simulation | `POST /api/simulate` → flood `Polygon` + exposure counts. |
| Impact Summary card | Submerged hospitals and cut-off roads counts. |
| Generate Advisory | `POST /api/advisory` → `Modal` with evacuation priorities + SMS text. |
| Copy SMS | `expo-clipboard`. |

**Deviation from the request, agreed:** the API call fires on
`onSlidingComplete`, not `onValueChange`. `onValueChange` fires on every pixel
of a drag — dozens of full flood computations per swipe. The category label
still tracks live during the drag. `Rules.md` already forbids calling *Gemini*
on slider change for exactly this class of reason; the same reasoning applies
to the local simulation.

**Deferred, not in this slice:** the Remal track `Polyline`. It is a
`PRD.md` must-have but was not requested here, and it needs a static serve
route. `PRD.md`'s two other additions (substations) are in.

---

## 7. Dependencies

Added to `requirements.txt`:

`fastapi`, `uvicorn[standard]`, `pydantic`, `rasterio`, `shapely`,
`google-genai`, `httpx` (for `TestClient`).

`geopandas`, `osmnx`, and `networkx` are **not** added — routing is not in this
slice, and geopandas is unnecessary (§3.4). `earthengine-api` stays in
`requirements.txt` but `fetch_dem.py` no longer imports it.

## 8. Secrets

`GEMINI_API_KEY` is read from the environment. A gitignored `.env` is used for
local runs. `.env` is **not** currently in `.gitignore` and will be added. The
key the user supplied in chat is treated as exposed and is never written to a
tracked file; rotating it after testing is recommended.

---

## 9. Testing

TDD via the `test-driven-development` skill — failing test first, per module.
**No test requires network access.**

| Module | Tests |
|---|---|
| `fetch_dem` | tile-index math against the verified z=12 values; Terrarium decode against a hand-computed pixel; mosaic assembly and CRS/transform on a 1-tile fixture |
| `surge` | IMD category boundaries; clamp floors negatives and caps the extreme; MAE surfaced in the result |
| `flood` | synthetic plane DEM with a known answer; decay monotonicity (inland distance never increases head); ocean connectivity enforced (an inland depression is not flooded); area sanity |
| `exposure` | point-in-polygon and line-intersection counts against hand-built fixtures |
| API | `TestClient`: valid `FeatureCollection` with coordinates inside the bbox; exposure counts; 422 on out-of-range wind; `/api/advisory` returns the `DistrictAdvisory` shape via the fallback path; SMS length validation |
| Mobile | `tsc --noEmit` typecheck. **No runtime test** — see §10. |

The full 289-tile download is a one-off manual run, not a test.

---

## 10. Stated limitations

1. **The surge model is statistically weak** — LOOCV MAE 2.36 m on values of
   0.6–2.9 m, n=4. Clamped, MAE-exposed, and labelled an estimate everywhere it
   surfaces. This is the most significant weakness in the slice.
2. **`L = 3000 m` decay length is a tuned assumption**, not fitted or measured.
3. **Road cut-off is intersection, not connectivity** (§3.4).
4. **Block-level evacuation priorities are limited to real OSM place names**,
   since no block dataset exists.
5. **The Expo app cannot be run here** — no simulator or device. It will be
   typechecked only. Runtime verification requires a phone.
6. **`gemini-3.7-flash` is not the newest Flash available** (3.8 exists). Kept
   per `Rules.md`; flagged for human decision.

## 11. Out of scope

Evacuation routing (Dijkstra over `osmnx`), shelter allocation (LP), at-risk
population estimation, curated shelter locations, Remal track rendering,
persisted history, auth, location permission. All already tracked in `Task.md`.
