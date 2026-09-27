# Module A — Data Pipeline & Surge ML Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-fetch all real data for the Cyclone Remal case study (track, OSM infrastructure, SRTM DEM) into `/data`, and train + serialize the surge regression model, printing its leave-one-out MAE.

**Architecture:** Four standalone fetch/train scripts under `backend/data_pipeline/`. Each script pairs pure, testable transform functions (CSV→GeoJSON, Overpass JSON→GeoJSON, LOOCV training) with a thin `main()` that does network I/O and writes files. Nothing in `/data` is ever fetched live at runtime — this session produces those static files.

**Tech Stack:** Python 3.13 (venv at `venv/`), requests, scikit-learn + joblib + numpy, earthengine-api, pytest. GeoJSON written as plain dicts (no geopandas in Module A).

**Spec:** `CLAUDE.md` (= project brief), `Architecture.md`, `Rules.md`, `Task.md` at repo root. This plan implements only the Module A section of `Task.md`.

## Global Constraints

- Target bbox, verbatim: `(21.30, 87.80, 22.60, 89.20)` (min_lat, min_lon, max_lat, max_lon).
- Case study anchor: Cyclone Remal, May 2024; anchor surge **1.2 m**.
- IBTrACS URL: `https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship/v04r00/access/csv/ibtracs.NI.list.v04r00.csv`; filter `NAME == "REMAL"`, `SEASON == "2024"`; skip row index 1 (units row).
- Overpass queries (pre-fetch only, never live at runtime): `amenity~hospital|clinic`, `power~substation|plant`, `highway~motorway|trunk|primary|secondary`, using `out geom;` so ways carry coordinates.
- DEM: `ee.Image("USGS/SRTMGL1_003")`, 30 m, clipped to bbox, exported via `getDownloadURL`.
- Surge training points, verbatim — features `[wind_kmph, forward_speed_kmph, approach_angle_flag] -> surge_m`:
  - Remal: `[115, 16, 1] -> 1.2`
  - Helen: `[105, 13, 0] -> 1.6`
  - Lehar: `[95, 20, 1] -> 2.9`
  - Mandous: `[70, 14, 0] -> 0.6`
- Model: `sklearn.linear_model.LinearRegression`, evaluated with `LeaveOneOut` CV, serialized to `data/surge_model.pkl` via joblib; training must print LOOCV MAE.
- `Rules.md`: every historical number traceable to a named source; estimates labelled as estimates in code comments.
- `.gitignore` must cover `venv/`, `__pycache__/`, all `*.geojson`/`*.tif`/`*.pkl` except under `data/`.

## Review Focus

1. **IBTrACS CSV quirks** — units row (row index 1) and older REMALs from other seasons: test pins `SEASON == "2024"` filter and units-row skip.
2. **Overpass failure modes** — HTTP 429/timeout returns partial or zero elements: script must retry once with backoff and fail loudly (non-zero exit, no empty files written) rather than silently committing empty GeoJSON.
3. **GEE not authenticated / download size limits** — `fetch_dem.py` must exit with a clear auth-remediation message on `ee` init failure, and fall back from 30 m to 90 m scale if the 30 m export is rejected. Outcome (success or blocker) recorded in MEMORY.md.
4. **Tiny-sample regression** — with 4 points and 3 features the full-fit interpolates exactly, but LOOCV errors can be large: test asserts only that MAE is finite and non-negative, plus an in-sample sanity check that fitted prediction for Remal `[115,16,1]` ≈ 1.2 (tolerance 0.1).
5. **Wind-speed units** — training table wind values are km/h (per spec) while IBTrACS `USA_WIND` is knots: track GeoJSON keeps raw IBTrACS values with the source column names in properties, so no unit is ever silently converted.

---

### Task 1: Repo scaffolding & environment

**Files:**
- Create: `AGENTS.md`, `GEMINI.md` (symlinks to `CLAUDE.md`)
- Create: `data/`, `backend/data_pipeline/`, `tests/` directories
- Create: `requirements.txt`
- Modify: `.gitignore` (create at repo root — none exists in `Cyclone app/`)

**Interfaces:**
- Produces: `venv/` with requests, numpy, scikit-learn, joblib, earthengine-api, pytest installed; all later tasks run with `venv/bin/python` / `venv/bin/pytest`.

- [ ] **Step 1: Create structure + symlinks**

```bash
cd "/Users/priyanujgoswami/Cyclone app"
mkdir -p data backend/data_pipeline tests docs
ln -s CLAUDE.md AGENTS.md && ln -s CLAUDE.md GEMINI.md
```

`.gitignore` contents: `venv/`, `__pycache__/`, `*.pyc`, then data-fetch artifacts outside `data/`:
```
*.geojson
*.tif
*.pkl
!data/**
```
(equivalently: ignore them everywhere, un-ignore `data/`; simplest correct form is to scope ignores by adding `!data/*.geojson` etc. — implementer picks one that `git check-ignore` proves right.)

- [ ] **Step 2: Create `requirements.txt` + venv, verify install**

`requirements.txt`: `requests`, `numpy`, `scikit-learn`, `joblib`, `earthengine-api`, `pytest` (unpinned OK for Module A).

Run: `python3 -m venv venv && venv/bin/pip install -r requirements.txt`
Expected: `venv/bin/python -c "import sklearn, requests, ee, joblib, numpy"` exits 0.

- [ ] **Step 3: Commit (if git initialized — see open question)**

`git add -A && git commit -m "chore: module A scaffolding"`

---

### Task 2: `fetch_ibtracs.py` → `data/remal_track.geojson`

**Files:**
- Create: `backend/data_pipeline/fetch_ibtracs.py`
- Test: `tests/test_fetch_ibtracs.py`

**Interfaces:**
- Produces:
  - `parse_ibtracs_rows(csv_text: str) -> list[dict]` — rows for REMAL/2024 only, units row excluded; each dict has `iso_time: str`, `lat: float`, `lon: float`, `usa_wind_kt: float`.
  - `track_to_geojson(rows: list[dict]) -> dict` — FeatureCollection with one `LineString` feature (geometry from rows in time order, properties `{name: "REMAL", season: "2024", source: "IBTrACS v04r00", wind_units: "knots"}`) plus one `Point` feature per row (properties include `iso_time`, `usa_wind_kt`).
  - `main() -> None` — downloads CSV via requests (60 s timeout), writes `data/remal_track.geojson`.

- [ ] **Step 1: Write the failing test**

Fixture: inline CSV string containing a header row, the units row (row index 1), one 1983-era REMAL-named row (different SEASON), and ≥3 REMAL 2024 rows with real-ish values (e.g. `2024-05-24 00:00:00, 16.5N/87.9E ... ` — implementer uses the actual IBTrACS column layout: `SID,SEASON,NUMBER,BASIN,...,NAME,...,ISO_TIME,...,LAT,LON,...,USA_WIND,...`).

```python
def test_parse_filters_remal_2024_and_skips_units_row():
    rows = parse_ibtracs_rows(FIXTURE_CSV)
    assert all(r["iso_time"].startswith("2024") for r in rows)
    assert len(rows) == 3
    assert rows[0]["usa_wind_kt"] != "kt"  # units row leaked → str, not float

def test_track_to_geojson_structure():
    fc = track_to_geojson(parse_ibtracs_rows(FIXTURE_CSV))
    types = sorted(f["geometry"]["type"] for f in fc["features"])
    assert types == ["LineString"] + ["Point"] * 3
```

- [ ] **Step 2: Run tests, verify they fail** — `venv/bin/pytest tests/test_fetch_ibtracs.py -v` → ImportError/failure.

- [ ] **Step 3: Implement `fetch_ibtracs.py`**

Use stdlib `csv` over the response text (no pandas). Filter, cast LAT/LON/USA_WIND to float (drop rows with empty LAT/LON), sort by ISO_TIME, build GeoJSON, `json.dump` with `indent=2`.

- [ ] **Step 4: Run tests, verify pass.**

- [ ] **Step 5: Run the fetch for real**

Run: `venv/bin/python backend/data_pipeline/fetch_ibtracs.py`
Expected: `data/remal_track.geojson` exists, contains REMAL 2024 only; spot-check first/last timestamps bracket the May 24–27 2024 landfall window and max `usa_wind_kt` is in the 55–70 kt range (≈100–130 km/h, matching the real 110–120 km/h reported).

- [ ] **Step 6: Commit** — `git add backend/data_pipeline/fetch_ibtracs.py tests/test_fetch_ibtracs.py data/remal_track.geojson`.

---

### Task 3: `fetch_osm_infra.py` → hospitals/substations/roads GeoJSON

**Files:**
- Create: `backend/data_pipeline/fetch_osm_infra.py`
- Test: `tests/test_fetch_osm_infra.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `BBOX = (21.30, 87.80, 22.60, 89.20)`
  - `overpass_to_geojson(elements: list[dict]) -> dict` — `node` → Point Feature (all tags as properties); `way` → LineString Feature from its `geometry` array; elements without coordinates are skipped.
  - `fetch_overpass(query: str) -> list[dict]` — POST to `https://overpass-api.de/api/interpreter`, one retry with 5 s backoff on 429/timeout; raises on repeated failure.
  - `main()` — runs the three tag queries sequentially (hospitals: `amenity~"hospital|clinic"`; substations: `power~"substation|plant"`; roads: `highway~"motorway|trunk|primary|secondary"`), all with `out geom;` and the bbox, writes `data/hospitals.geojson`, `data/substations.geojson`, `data/roads.geojson`.

- [ ] **Step 1: Write the failing test**

```python
def test_overpass_nodes_and_ways_become_features():
    elements = [
        {"type": "node", "id": 1, "lat": 21.9, "lon": 88.1, "tags": {"amenity": "hospital"}},
        {"type": "way", "id": 2, "geometry": [{"lat": 21.9, "lon": 88.0}, {"lat": 22.0, "lon": 88.2}], "tags": {"highway": "primary"}},
        {"type": "way", "id": 3, "tags": {"highway": "trunk"}},  # no geometry → skipped
    ]
    fc = overpass_to_geojson(elements)
    assert [f["geometry"]["type"] for f in fc["features"]] == ["Point", "LineString"]
    assert fc["features"][0]["properties"]["amenity"] == "hospital"
```

- [ ] **Step 2–4:** fail → implement (stdlib `json`, `requests.post` with `data={"data": query}`) → pass.

- [ ] **Step 5: Run the fetch for real**

Run: `venv/bin/python backend/data_pipeline/fetch_osm_infra.py`
Expected: three files in `data/`; print feature counts per file. Sanity expectation: hospitals/clinics ≥ 10 features, roads ≥ 20 ways for this bbox; substations may be sparse (≥1 acceptable — record count in MEMORY.md either way). On Overpass failure after retry: exit non-zero, no file written, blocker logged.

- [ ] **Step 6: Commit.**

---

### Task 4: `fetch_dem.py` → `data/dem.tif` (GEE)

**Files:**
- Create: `backend/data_pipeline/fetch_dem.py`

**Interfaces:**
- Produces: `main() -> None` writing `data/dem.tif`. No pure functions consumed by other tasks.

- [ ] **Step 1: Implement**

`ee.Initialize()`; build `ee.Image("USGS/SRTMGL1_003").clip(ee.Geometry.Rectangle([87.80, 21.30, 89.20, 22.60]))`; request `getDownloadURL({"scale": 30, "region": bbox, "format": "GEO_TIFF"})`; download the returned zip with requests, extract the `.tif` member, save as `data/dem.tif`. On GEE rejection of 30 m export, retry at 90 m and print which scale was used. On `ee.Initialize()` auth failure, print the exact remediation (`earthengine authenticate` / project registration) and exit non-zero. No unit test (external service) — verification is the file check below.

- [ ] **Step 2: Verify output**

Run: `venv/bin/python -c "d=open('data/dem.tif','rb').read(4); assert d[:2] in (b'II', b'MM') and d[2:4] in (b'\x2a\x00', b'\x00\x2a'), 'not a TIFF'"` and confirm size > 100 KB.
Expected: TIFF magic bytes valid.

- [ ] **Step 3: Commit, and record scale used (or auth blocker) in MEMORY.md.**

---

### Task 5: `train_surge_model.py` → `data/surge_model.pkl` + LOOCV MAE

**Files:**
- Create: `backend/data_pipeline/train_surge_model.py`
- Test: `tests/test_train_surge_model.py`

**Interfaces:**
- Produces:
  - `training_table() -> tuple[np.ndarray, np.ndarray]` — `(X, y)` with exactly the four spec points above; each storm's source noted in a comment (IMD/IBTrACS per project brief; surge values as given in `CLAUDE.md`).
  - `train(X: np.ndarray, y: np.ndarray) -> tuple[LinearRegression, float]` — returns full-fit model and LOOCV MAE (`LeaveOneOut` + `cross_val_predict`, scoring via absolute error).
  - `main()` — prints per-fold prediction vs actual and `LOOCV MAE: <value> m`, fits on all data, `joblib.dump`s `{"model": model, "features": ["wind_kmph", "forward_speed_kmph", "approach_angle_flag"], "loo_mae": mae}` to `data/surge_model.pkl`.

- [ ] **Step 1: Write the failing test**

```python
def test_training_table_matches_spec():
    X, y = training_table()
    assert X.shape == (4, 3) and y.shape == (4,)
    assert list(X[0]) == [115, 16, 1] and y[0] == 1.2

def test_loo_mae_finite_and_full_fit_interpolates_remal():
    X, y = training_table()
    model, mae = train(X, y)
    assert np.isfinite(mae) and mae >= 0
    assert abs(model.predict([[115, 16, 1]])[0] - 1.2) < 0.1
```

- [ ] **Step 2–4:** fail → implement → pass.

- [ ] **Step 5: Run training for real**

Run: `venv/bin/python backend/data_pipeline/fetch_ibtracs.py` — no — run: `venv/bin/python backend/data_pipeline/train_surge_model.py`
Expected output includes `LOOCV MAE: <number> m` (record this number in MEMORY.md); `data/surge_model.pkl` written.

- [ ] **Step 6: Commit.**

---

### Task 6: Session closeout (MEMORY.md + Task.md)

**Files:**
- Modify: `MEMORY.md` (Status by module, What exists, Blockers, Next step, Session log)
- Modify: `Task.md` (check off completed Module A boxes)

- [ ] **Step 1:** Update `MEMORY.md`: Module A → "In progress" or "Done" per actual outcome; list every created file under "What actually exists"; note blockers (GEE auth, Overpass, actual substation/hospital feature counts, LOOCV MAE value); rewrite "Next step" pointing at Module B (BFS flood propagation over `data/dem.tif`, road graph from `data/roads.geojson`, shelter curation, `scipy.optimize.linprog` allocation — referencing `Task.md` Module B checkboxes); add Session log entry dated 2026-09-27, tool = Z.Code, built = Module A pipeline.
- [ ] **Step 2:** Check off completed Module A items in `Task.md`.
- [ ] **Step 3: Commit.**

## Self-review notes

- Spec coverage: all 7 Module A deliverables (4 scripts, 6 data files, LOOCV print) map to Tasks 2–5; stretch RSMC point-gathering is explicitly out of scope this session.
- Open questions for the human before execution: (1) is a Google Earth Engine account/credentials available, (2) may I `git init` inside `Cyclone app/` (currently only an outer home-dir repo tracks it) — if yes I commit per task, if no I skip all commit steps.
