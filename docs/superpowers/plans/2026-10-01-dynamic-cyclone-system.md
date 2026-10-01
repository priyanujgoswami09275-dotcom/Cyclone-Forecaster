# Dynamic Cyclone System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-case-study Remal app into a system that loads any North Indian Ocean cyclone from IBTrACS, attempts a live ATCF feed without depending on a manually supplied mirror, predicts storm peak intensity with a real ML model on real features, compares scenarios, and adds an evidence-grounded AI Risk Analyst — while leaving the surge law, the simulation engines, Gemini integration, deployment and the DESIGN.md visual system untouched.

**Architecture:** A `CycloneSource` protocol with two implementations (IBTrACS historical, ATCF live) produces one normalized `CycloneRecord`. A `ScenarioContext` of `(cyclone_id, scenario_id)` keys every cache, so a cache entry can never be served for a different storm. The ML layer predicts storm peak intensity from real IBTrACS features and is reported *beside* the deterministic surge law, never in place of it. When no public NI-basin ATCF source is reachable the live provider returns an explicit `live_unavailable` state carrying source, HTTP status, timestamps and a plain-language limitation — and never substitutes historical or Remal data.

**Tech Stack:** Python 3.13 + FastAPI (existing), scikit-learn + numpy (existing deps, already in `venv`), IBTrACS v04r01 NI CSV (in-repo, 27 MB), ATCF comma-delimited b-decks, Open-Meteo Forecast API (no key), Expo SDK 57 / React Native + TypeScript (existing), node:test, pytest.

**Spec:** This document. Derived from a direct instruction to build the pipeline
`historical/live cyclone provider → normalized cyclone record → ML storm-peak-intensity layer → existing surge/flood/exposure/routing/allocation → scenario comparison → Gemini NLP → AI Risk Analyst`.

---

## Global Constraints

These apply to every task. A task's requirements implicitly include them.

- **The deterministic surge law is authoritative and is not modified.** `surge_m = 1.2 * (wind_kmph/115)**2` in `backend/simulation/surge.py` keeps its exact values. ML output never flows into it. Every ML number ships labelled as an estimate with its own limitation string.
- **No fabricated data of any kind.** No synthetic live cyclones, no invented forecast positions, no synthesized surge or wind labels, no placeholder ML targets. If a data source does not contain something, the response says so.
- **Historical fallback for live is forbidden.** When the live provider cannot reach a source, it returns `live_unavailable`. It must never fall back to Remal, to the IBTrACS catalogue, or to any historical record.
- **IBTrACS is filtered to `BASIN == 'NI'` during ingestion.** The file also contains `WP` (4,525 rows) and `NA` (482 rows); those must never appear. The file's single junk row (`BASIN == ' '`) is dropped.
- **IBTrACS `LANDFALL` is not a boolean.** It holds `DIST2LAND` in nautical miles on nearly every NI row (values observed: `0`, `10`, `15`, `22`, `24`, `30`, `33`, `54`, `67`, `78`, `100`, `123`, `246`, `570`, `583`, `586`, and blank on 1,857 rows). Treating it as a flag silently mislabels the training set. See Task 6.
- **No new API key and no paid service.** Open-Meteo is called keyless. Nothing in this plan adds a credential.
- **`Rules.md` and `CLAUDE.md` case-study wording stays accurate.** Remal remains the default and the documented case study; other cyclones are additions.
- **Every scientific disclosure is preserved verbatim in substance.** "Screening estimate not forecast", "track is history", "shelter placeholders", "population estimates", "roads intersected ≠ impassable" all still ship. The new ones are additive.
- **No generic dashboard redesign.** New UI reuses `mobile/theme.ts`, the existing `PanelStep`/`GhostButton`/`PrimaryButton` primitives, the existing SVG map on Web and `LeafletMap` on native, and changes no DESIGN.md token.
- **Cache keys must include both `cyclone_id` and `scenario_id`.** A cache keyed on category alone must not be able to serve a different storm's or scenario's result.
- **Existing tests are never deleted or weakened.** 352 Python passed / 3 skipped and 291 mobile passed at plan-writing time; all must still pass at the end.
- **No secrets.** Nothing in this plan reads or writes `.env`, and no key is printed.

---

## Review Focus

Five input classes or failure modes the spec implies that no task's happy path exercises. Each gets a test in the task that owns the code, listed in that task's steps.

1. **A cache entry computed for one cyclone being served for another** — the single highest-cost failure, because it produces a confidently wrong map rather than an error. Pinned in Task 5.
2. **IBTrACS missing-value sentinels** (`-1`, `-9999`, blank, and a literal single space) read as real numbers. Observed empirically: `USA_WIND` has blanks, and `WMO_PRES` is present on only 10% of NI rows. Pinned in Task 2 and Task 6.
3. **The live provider silently degrading to historical data** when a source 404s, 403s, times out, or returns a non-ATCF body. Pinned in Task 3.
4. **Open-Meteo returning 400** for an invalid variable, an out-of-range date, or a bad key — confirmed live — and the app treating a 400 as "no weather" rather than as a failure to disclose. Pinned in Task 4.
5. **Two cyclones whose names collide or whose `SID`s differ only in basin** (e.g. `2024145N14087` vs a WP-basin SID) being conflated into one catalogue entry. Pinned in Task 1.

---

## Architecture Decision Record

Decisions the implementer cannot make alone. Each was verified against real data or a live API during planning, not assumed.

### Why the live provider reports `live_unavailable` by default

Measured 2026-10-01, every candidate from a server-side fetch:

| Source | Result |
|---|---|
| `nrlmry.navy.mil/atcf_web/docs/current_storms.txt` | **403** |
| `nrlmry.navy.mil/atcf_web/current_storms.txt` | **403** |
| `nrlmry.navy.mil/atcf_web/docs/` | **403** |
| `metoc.navy.mil/jtwc/jtwc.html` | **403** |
| `cira.colostate.edu/atlanticos/` | **403** |
| `nhc.noaa.gov/data/atcf/current_storms.txt` | **404** |
| `nhc.noaa.gov/CurrentStorms.json` | 200, but **ATL/EP only**, no `io*` ids |
| `nhc.noaa.gov/atcf/archive/{2010,2015,2019..2025}` | 200, but **zero `io*.dat.gz`** in every year (132–208 files/yr, prefixes `aal`/`acp`/`aep` only) |
| `nhc.noaa.gov/atcf/archive/2026/` | **404** |
| `rsmcnewdelhi.imd.gov.in/json/` and `/pdf/` | **404** |
| `mausam.imd.gov.in/responsive/cyclone.php` | **404** |
| `rammb-data.cira.colostate.edu` | **DNS NXDOMAIN** |

So the default endpoint list is real and correct, and it fails honestly. The ATCF parser is written and tested against **real bytes** (fetched from NHC) so it is correct the day a source opens, and the endpoint list is environment-configurable so no code change is needed then.

### ATCF wire format, verified against real bytes

Fetched and parsed `https://ftp.nhc.noaa.gov/atcf/archive/2025/aal012025.dat.gz` (11,295 lines). It is **comma-delimited**, not the fixed-column layout, and the leading numeric column is a **lead-time offset in hours** (`-24` to `204`), not latitude:

```
AL, 01, 2025062218, 01, CARQ, -24, 302N,  573W,  20,    0, LO,  34, AAA, ...
 ^0  ^1  ^2         ^3  ^4   ^5   ^6    ^7    ^8    ^9  ^10 ^11
```

| Index | Field | Notes |
|---|---|---|
| 0 | basin | `AL`, `WP`, `EP`, `CP`, `IO` |
| 1 | storm number | `01`.. |
| 2 | `YYYYMMDDHH` | UTC, no minutes |
| 3 | landmark | `01`, `03` observed; **not** a landfall flag |
| 4 | name | `CARQ`, `GFSO`; may be blank |
| 5 | **lead time (hours)** | integer, can be negative |
| 6 | latitude | `302N` → 30.2 N; implicit decimal before the last digit |
| 7 | longitude | `573W` → 57.3 W; **sign comes from the hemisphere letter, not a sign character** |
| 8 | sustained wind | knots |
| 9 | pressure | hPa |
| 10 | nature | `TD`, `TS`, `LO`, `DB`, `EX`, `PT`, `XX`, blank |

Field 5 was verified *not* to be latitude by two independent tests: `|f[5]| ≈ int(f[6])` held on only 60 of 11,295 rows, while its values fall on a `-24..204` hourly grid. A parser that read field 5 as latitude would place AL01 south of 24°S on its first fix.

Longitude has no sign character, so `parse_hemispheric_coordinate` is the one place that decides sign, and it is tested for both hemispheres in both positions.

### IBTrACS NI training-set shape, measured

`ibtracs.NI.list.v04r01.csv`, 174 columns, 62,861 rows, `csv.field_size_limit` must be raised.

| Quantity | Value |
|---|---|
| Rows with `BASIN == 'NI'` | 57,852 (of 62,861; the rest are `WP` 4,525 and `NA` 482) |
| `SUBBASIN` | `BB` (Bay of Bengal) 42,426, `AS` (Arabian Sea) 15,426 |
| Seasons | 1842–2026, 153 distinct |
| NI storms | 1,859 |
| NI storms ≥1970 | 610 |
| ≥1970 with any `USA_WIND ≥ 0` | **300** |
| ≥1970 with `USA_WIND` on a `LANDFALL`-coded row | **299** |
| `USA_WIND` fill rate (all NI rows) | 21.7% |
| `STORM_SPEED` / `STORM_DIR` / `DIST2LAND` / `LAT` / `LON` on the target row | **100%** |
| `USA_PRES` on the target row | 48% |
| `USA_RMW` on the target row | 28% |

**n = 299, median 50 kt, 33% at or above 64 kt.** That is small enough that leave-one-out CV is the only defensible evaluation, and small enough that a flat median baseline is a serious competitor. Task 6 makes beating it a gate, not a hope.

Remal's own `USA_WIND` peak is 60 kt, against a documented landfall of 110–120 km/h (59–65 kt) — the IBTrACS value is consistent with the IMD figure, which is a useful sanity check on the loader.

### Open-Meteo, verified against the official docs

The user-provided Python example was a usage example. Verified parameters from `https://open-meteo.com/en/docs` plus live probes on 2026-10-01:

- `hourly=` accepts `wind_speed_10m`, `wind_gusts_10m`, `wind_direction_10m`, `pressure_msl`, `precipitation`, `cape`, `weather_code`, and ~50 more. Confirmed live for the five requested.
- `wind_speed_10m` is **10-minute mean** wind. Gusts are the **preceding-hour maximum**, not an instantaneous value — so a gust is never presented as a sustained wind.
- `forecast_days` range is **0–16** (default 7). `past_days` is **0–92**.
- `start_date`/`end_date` are bounded to roughly the last 3.5 months; `2027-06-01` returns 400 `Parameter 'start_date' is out of allowed range`.
- **No API key is needed** for non-commercial use. Supplying an invalid one returns **400**, so the client must send none.
- Errors are **HTTP 400** with `{"error": true, "reason": "..."}`. Confirmed for an unknown variable, an out-of-range latitude, and an out-of-range date.
- `Access-Control-Allow-Origin: *` **is** returned when an `Origin` header is sent; it was absent on a request without one. The backend calls server-side, so this is informational.
- `current=` returns `interval: 900` seconds.

`cell_selection` defaults to `land`. Over the Bay of Bengal the storm is over water, so Task 4 sets `cell_selection=sea`, which the docs describe as preferring grid cells on water — the physically correct choice for a marine forecast, and it must be disclosed because it changes which grid cell is used.

### Scenario definition

A scenario is **a wind in km/h**, never a free-text strength. `ScenarioContext(cyclone_id, scenario_id)` resolves `scenario_id` to exactly one `wind_kmph` through the *same* `IMD_BANDS` table the existing code uses, so a category index and a scenario cannot drift apart. The ML storm-peak estimate is a **second, separately labelled** figure for the same scenario and never feeds the surge law.

### Cache isolation

Four `lru_cache` sites currently key on category alone (`surge_for_category`, `flood_for_category`, `populations_for_category`, `shelters_for_category`, plus `allocation_for_category`). A cache keyed on category cannot distinguish two cyclones whose scenarios happen to resolve to the same wind, and cannot distinguish an `observed` scenario from a `cat4` scenario that resolve to the same number. Task 5 re-keys all of them on `(cyclone_id, scenario_id)`.

---

## File Structure

### New backend files

| Path | Responsibility |
|---|---|
| `backend/cyclones/__init__.py` | Package marker, re-exports the registry and the record type |
| `backend/cyclones/base.py` | `CycloneRecord`, `CycloneWaypoint`, `CycloneSource` protocol, `LiveStatus` |
| `backend/cyclones/historical.py` | `IbtracsSource` — reads the committed CSV, NI-filtered |
| `backend/cyclones/live.py` | `AtcfLiveSource` — configurable endpoints, ATCF parse, `live_unavailable` |
| `backend/cyclones/atcf.py` | `parse_atcf_line`, `parse_hemispheric_coordinate` — pure, no I/O, no network |
| `backend/cyclones/registry.py` | `CycloneRegistry`: id → record, historical catalogue build, live probe |
| `backend/cyclones/scenarios.py` | `ScenarioContext`, `Scenario`, `resolve_wind_kmph` |
| `backend/ml/__init__.py` | Package marker |
| `backend/ml/storm_peak_intensity.py` | Feature build, LOOCV, fit, `StormPeakEstimate`, baseline gate |
| `backend/weather/__init__.py` | Package marker |
| `backend/weather/open_meteo.py` | `OpenMeteoClient`, param build, unit handling, 400 handling |
| `backend/data_pipeline/ingest_ibtracs_ni.py` | One-shot CLI: stream the placed CSV → `data/cyclones/catalogue.json` |

### New frontend files

| Path | Responsibility |
|---|---|
| `mobile/cycloneModel.ts` | `Cyclone`, `Scenario`, `LiveState` types, state labels, freshness text |
| `mobile/scenarioCompare.ts` | Comparison table maths, delta formatting, honesty guards |
| `mobile/components/CyclonePicker.tsx` | Cyclone + source picker (`GhostButton`-based) |
| `mobile/components/ScenarioComparePanel.tsx` | The comparison sheet |
| `mobile/components/RiskAnalystPanel.tsx` | AI Risk Analyst output, evidence chips |
| `mobile/apiCyclones.ts` | Typed client for the new endpoints |

### New tests

`tests/test_cyclones_base.py`, `tests/test_ibtracs_ni.py`, `tests/test_atcf.py`,
`tests/test_live_source.py`, `tests/test_scenarios.py`, `tests/test_cache_isolation.py`,
`tests/test_storm_peak_intensity.py`, `tests/test_open_meteo.py`, `tests/test_new_endpoints.py`,
`tests/test_risk_analyst.py`, and `mobile/tests/cycloneModel.test.mjs`,
`mobile/tests/scenarioCompare.test.mjs`.

### Modified files

`backend/main.py` (new endpoints, optional params, cache re-keying, dispatch), `backend/ai/advisory.py` (cyclone context + Risk Analyst prompt), `mobile/api.ts` (optional params on existing calls), `mobile/components/MapScreen.tsx`, `mobile/components/MapScreen.web.tsx` (cyclone/scenario state, both platforms), `README.md`, `CLAUDE.md`, `MEMORY.md`, `Design.md`.

### Committed data

`data/cyclones/catalogue.json` — generated by `ingest_ibtracs_ni.py` from the already-present CSV. Deterministic, no network. Remal's entry must reproduce today's `/track` payload byte-for-byte in content.

---

## Task 1: The normalized cyclone record

The vocabulary every later task speaks. Nothing else can be written until this exists.

**Files:**
- Create: `backend/cyclones/__init__.py`, `backend/cyclones/base.py`
- Test: `tests/test_cyclones_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CycloneWaypoint(iso_time: str, latitude: float, longitude: float, wind_kmph: float | None, wind_reported: bool, pressure_hpa: float | None, nature: str | None) -> CycloneWaypoint` (frozen dataclass)
  - `CycloneRecord(cyclone_id: str, name: str, season: int, basin: str, subbasin: str | None, source: str, observed: bool, waypoints: tuple[CycloneWaypoint, ...], fetched_at: str, data_through: str | None, peak_wind_kmph: float | None, limitation: str) -> CycloneRecord` (frozen dataclass)
  - `CycloneSource` — `Protocol` with `identifier: str` and `async def fetch(self, cyclone_id: str | None = None) -> CycloneRecord | None`
  - `LiveStatus(status: str, source: str, http_status: int | None, reason: str, checked_at: str, endpoints: tuple[str, ...]) -> LiveStatus` where `status` ∈ `{"available", "live_unavailable", "no_active_storm"}`
  - `const LIVE_UNAVAILABLE_REASON: str` — the plain-language sentence

- [ ] **Step 1: Write the failing tests**

`tests/test_cyclones_base.py`:

```python
def test_peak_wind_ignores_unreported_fixes():
    # A waypoint with wind_reported False must not contribute, and must not
    # become 0. This is the rule /track already implements (MEMORY.md §31).
    ws = (
        CycloneWaypoint("2024-05-25T12:00:00Z", 19.2, 89.2, 35.0, True, None, "TS"),
        CycloneWaypoint("2024-05-25T15:00:00Z", 19.4, 89.2, None, False, None, "TS"),
    )
    assert peak_wind_kmph(ws) == 35.0

def test_peak_wind_is_none_when_nothing_was_reported():
    ws = (CycloneWaypoint("2024-05-25T12:00:00Z", 19.2, 89.2, None, False, None, "TS"),)
    assert peak_wind_kmph(ws) is None

def test_live_status_serialises_with_source_and_timestamp():
    # Every field a caller needs to judge staleness, even on failure.
    s = LiveStatus("live_unavailable", "nrlmry.navy.mil", 403,
                   "The source refused the request.", "2026-10-01T00:00:00Z",
                   ("https://www.nrlmry.navy.mil/atcf_web/docs/current_storms.txt",))
    d = s.to_dict()
    assert d["status"] == "live_unavailable"
    assert d["http_status"] == 403
    assert d["checked_at"] == "2026-10-01T00:00:00Z"
    assert d["endpoints"]

def test_live_unavailable_reason_names_the_limitation_and_promises_no_substitute():
    r = LIVE_UNAVAILABLE_REASON
    assert "live" in r.lower()
    assert "historical" in r.lower()   # says what it will NOT do
    assert "not" in r.lower()
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_cyclones_base.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cyclones'`

- [ ] **Step 3: Implement `backend/cyclones/base.py`**

Frozen dataclasses exactly as in Interfaces. `peak_wind_kmph(waypoints)` is a module function returning `max` over waypoints where `wind_reported` is true and `wind_kmph` is not None, else `None`.

`LIVE_UNAVAILABLE_REASON` must say, in plain language: the live feed could not be reached; no live cyclone is being shown; no historical or case-study cyclone is being substituted; the historical list and the deterministic simulation are unaffected; and name the timestamp of the attempt. One sentence per clause, no jargon, no file paths.

`LiveStatus.to_dict()` returns `status`, `source`, `http_status`, `reason`, `checked_at`, `endpoints`.

- [ ] **Step 4: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_cyclones_base.py -q`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add backend/cyclones tests/test_cyclones_base.py
git commit -m "feat(cyclones): the normalized record every later task speaks"
```

---

## Task 2: IBTrACS ingestion, strictly NI

Historical source of truth. Must reproduce Remal exactly and must not leak WP/NA rows.

**Files:**
- Create: `backend/cyclones/historical.py`, `backend/data_pipeline/ingest_ibtracs_ni.py`
- Test: `tests/test_ibtracs_ni.py`

**Interfaces:**
- Consumes: `CycloneRecord`, `CycloneWaypoint` from Task 1.
- Produces:
  - `DEFAULT_IBTRACS_PATH: Path` — repo-root `ibtracs.NI.list.v04r01.csv`
  - `ibtracs_number(value: str | None) -> float | None` — the single missing-value parser: returns `None` for blank, whitespace-only, `-1`, `-9999`, `-999`, and any non-numeric; returns the float otherwise
  - `read_ni_rows(path: Path = DEFAULT_IBTRACS_PATH) -> Iterator[dict[str, str]]` — yields only `BASIN == 'NI'`
  - `build_catalogue(path: Path = DEFAULT_IBTRACS_PATH, since: int = 1970) -> dict` — `{"generated_from": str, "generated_at": str, "cyclones": [CycloneRecord.to_dict(), ...]}`
  - `class IbtracsSource` implementing `CycloneSource`, with `identifier = "ibtracs_v04r01_ni"`
  - `REMAL_CYCLONE_ID = "2024145N14087"`

- [ ] **Step 1: Write the failing tests**

`tests/test_ibtracs_ni.py`. It declares its own
`REPO_ROOT = Path(__file__).resolve().parents[1]`, matching the convention in
the existing `tests/test_track.py` and `tests/test_overlays.py` — there is no
`backend/paths.py` to import from. Otherwise:

```python
def test_missing_value_sentinels_are_not_numbers():
    # Measured on the real file: blanks and -1 both occur.
    for bad in (None, "", " ", "  ", "-1", "-999", "-9999", "-1.0", "n/a"):
        assert ibtracs_number(bad) is None, bad
    assert ibtracs_number("60") == 60.0
    assert ibtracs_number("0") == 0.0     # 0 is a real value, not a sentinel

def test_only_north_indian_ocean_rows_are_read():
    # The placed file also holds WP (4,525) and NA (482) rows.
    rows = list(read_ni_rows())
    assert rows, "the file must yield rows"
    assert {r["BASIN"] for r in rows} == {"NI"}

def test_the_junk_row_is_dropped():
    # The file has exactly one row with BASIN == ' '.
    rows = list(read_ni_rows())
    assert all(r["BASIN"].strip() == "NI" for r in rows)

def test_remal_is_present_and_reproduces_the_committed_track():
    cat = build_catalogue()
    ids = [c["cyclone_id"] for c in cat["cyclones"]]
    assert REMAL_CYCLONE_ID in ids
    remal = next(c for c in cat["cyclones"] if c["cyclone_id"] == REMAL_CYCLONE_ID)
    assert remal["name"] == "REMAL"
    assert remal["season"] == 2024
    assert remal["source"] == "ibtracs_v04r01_ni"
    assert remal["observed"] is True
    # The committed data/remal_track.geojson has 40 fixes; the catalogue must
    # agree, or /track and /cyclones would contradict each other.
    committed = json.loads((REPO_ROOT / "data" / "remal_track.geojson").read_text())
    assert len(remal["waypoints"]) == len(committed["features"]) - 1

def test_waypoints_keep_unreported_wind_as_unreported():
    cat = build_catalogue()
    remal = next(c for c in cat["cyclones"] if c["cyclone_id"] == REMAL_CYCLONE_ID)
    unreported = [w for w in remal["waypoints"] if not w["wind_reported"]]
    assert unreported, "Remal has fixes with no reported wind"
    assert all(w["wind_kmph"] is None for w in unreported)

def test_catalogue_is_deterministic():
    a = build_catalogue(); b = build_catalogue()
    strip = lambda c: [{k: v for k, v in w.items() if k != "fetched_at"} for w in c["waypoints"]]
    for x, y in zip(a["cyclones"], b["cyclones"]):
        assert (x["cyclone_id"], strip(x)) == (y["cyclone_id"], strip(y))

def test_two_basins_never_collide_into_one_id():
    # Review Focus #5. The placed file also holds WP and NA rows; if ingestion
    # ever stopped filtering, a WP SID would land in the catalogue. An IBTrACS
    # SID is "<season><number><basinLetter><lat><lon>", so the NI test is the
    # 12th character.
    cat = build_catalogue()
    ids = [c["cyclone_id"] for c in cat["cyclones"]]
    assert len(ids) == len(set(ids)), "duplicate cyclone_id in the catalogue"
    assert all(len(i) >= 12 for i in ids)
    assert {i[11] for i in ids} == {"N"}, "a non-NI SID leaked into the catalogue"


def test_a_wp_row_in_the_source_is_never_yielded():
    # A direct check on the reader, independent of what the catalogue built.
    with DEFAULT_IBTRACS_PATH.open(newline="") as fh:
        raw = list(csv.DictReader(fh))
    wp = [r for r in raw if r.get("BASIN") == "WP"]
    assert wp, "the source file must actually contain WP rows for this to test anything"
    yielded = {r["SID"] for r in read_ni_rows()}
    assert not (yielded & {r["SID"] for r in wp})
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_ibtracs_ni.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/cyclones/historical.py`**

`read_ni_rows` opens the CSV with `csv.field_size_limit(10_000_000)` (required: some columns exceed the default 131,072 limit), iterates `csv.DictReader`, and yields only rows whose `BASIN` strips to `"NI"`.

`build_catalogue` groups rows by `SID`, sorts each group by `ISO_TIME`, and maps each row to a `CycloneWaypoint`:

| Record field | Source column | Rule |
|---|---|---|
| `iso_time` | `ISO_TIME` | verbatim |
| `latitude` / `longitude` | `LAT` / `LON` | via `ibtracs_number`; a row missing either is **dropped**, not defaulted |
| `wind_kmph` | `USA_WIND` | knots → km/h via `× 1.852`, rounded to 1 dp; `None` when unreported |
| `wind_reported` | derived | `True` only when `USA_WIND` parsed to a non-negative float |
| `pressure_hpa` | `USA_PRES` | `None` when absent (48% fill) |
| `nature` | `NATURE` | verbatim; never mapped to an IMD category |

`cyclone_id` is the `SID` verbatim. `subbasin` is `SUBBASIN`. `name` is `NAME`, with `"UNNAMED"` preserved as-is rather than prettified. `since` filters on `SEASON`.

Record `limitation` must state: the track is observed history and not a forecast; it is not a prediction for any other storm; a fix with no reported wind is not calm; `USA_WIND` is the IBTrACS knots column; `NATURE` is IBTrACS's own classification and is not an IMD wind category.

- [ ] **Step 4: Implement `backend/data_pipeline/ingest_ibtracs_ni.py`**

A CLI: `python -m backend.data_pipeline.ingest_ibtracs_ni --out data/cyclones/catalogue.json`. Streams the CSV, writes the catalogue with `json.dump(..., indent=2, sort_keys=True)`. Takes no network action. Prints the storm count and the output path.

- [ ] **Step 5: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_ibtracs_ni.py -q`
Expected: PASS

Then: `venv/bin/python -m backend.data_pipeline.ingest_ibtracs_ni --out data/cyclones/catalogue.json`
Expected: prints a count and writes the file; `git add data/cyclones/catalogue.json`

- [ ] **Step 6: Commit**

```bash
git add backend/cyclones/historical.py backend/data_pipeline/ingest_ibtracs_ni.py \
        tests/test_ibtracs_ni.py data/cyclones/catalogue.json
git commit -m "feat(cyclones): IBTrACS ingestion, filtered strictly to the NI basin"
```

---

## Task 3: The ATCF live provider

A real parser and a real client. Fails honestly, substitutes nothing.

**Files:**
- Create: `backend/cyclones/atcf.py`, `backend/cyclones/live.py`
- Test: `tests/test_atcf.py`, `tests/test_live_source.py`
- Fixture: `tests/fixtures/aal012025_sample.dat` — 6 real lines copied verbatim from `https://ftp.nhc.noaa.gov/atcf/archive/2025/aal012025.dat.gz`, **committed**

**Interfaces:**
- Consumes: `CycloneRecord`, `CycloneWaypoint`, `CycloneSource`, `LiveStatus`, `LIVE_UNAVAILABLE_REASON` from Task 1.
- Produces:
  - `parse_hemispheric_coordinate(value: str) -> float | None` — `"302N"` → `30.2`, `"573W"` → `-57.3`, `None` on garbage
  - `parse_atcf_line(line: str) -> CycloneWaypoint | None` — one record or `None` for a malformed line
  - `parse_atcf(text: str, basin: str = "IO") -> tuple[CycloneWaypoint, ...]` — sorted by `iso_time`, **filtered to `basin`**
  - `DEFAULT_ATCF_ENDPOINTS: tuple[str, ...]` — the reachable-today list from the ADR
  - `AtcfLiveSource(endpoints: Sequence[str] = DEFAULT_ATCF_ENDPOINTS, timeout_s: float = 6.0, transport: Callable | None = None)`
  - `async def probe() -> LiveStatus`
  - `async def fetch(self, cyclone_id: str | None = None) -> CycloneRecord | None` — **`None` when unavailable, never a substitute**

- [ ] **Step 1: Write the failing ATCF parser tests**

```python
def test_leading_column_is_lead_time_not_latitude():
    # THE trap. Field 5 of a real ATCF line is an hour offset; reading it as
    # latitude puts AL01 at 302 S on its first fix.
    v = parse_atcf_line("AL, 01, 2025062218, 01, CARQ, -24, 302N,  573W,  20,    0, LO,  34, AAA,")
    assert v is not None
    assert v.latitude == pytest.approx(30.2)
    assert v.longitude == pytest.approx(-57.3)
    assert v.iso_time == "2025-06-22T18:00:00Z"
    assert v.wind_kmph == pytest.approx(20 * 1.852, rel=1e-3)
    assert v.pressure_hpa == 0.0 or v.pressure_hpa is None
    assert v.nature == "LO"

def test_longitude_sign_comes_from_the_hemisphere_not_a_sign_character():
    assert parse_hemispheric_coordinate("302N") == pytest.approx(30.2)
    assert parse_hemispheric_coordinate("302S") == pytest.approx(-30.2)
    assert parse_hemispheric_coordinate("573W") == pytest.approx(-57.3)
    assert parse_hemispheric_coordinate("573E") == pytest.approx(57.3)
    assert parse_hemispheric_coordinate("") is None
    assert parse_hemispheric_coordinate("abc") is None

def test_basin_filter_drops_non_io_storms():
    text = (FIXTURE / "aal012025_sample.dat").read_text()
    assert parse_atcf(text, basin="AL")           # keeps them
    assert parse_atcf(text, basin="IO") == ()     # drops them

def test_malformed_lines_are_skipped_not_fatal():
    text = "\n".join([
        "", "   ", "garbage", "IO, 01, 2025062218, 01, XYZ, 0, 302N, 573W, 20, 0, TS,",
        "IO, 01, notadate, 01, XYZ, 0, 302N, 573W, 20, 0, TS,",
    ])
    got = parse_atcf(text, basin="IO")
    assert len(got) == 1

def test_wind_never_invents_a_value_for_a_blank_field():
    v = parse_atcf_line("IO, 01, 2025062218, 01, XYZ, 0, 302N, 573W,   ,   , TS,")
    assert v.wind_kmph is None
    assert v.wind_reported is False
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_atcf.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/cyclones/atcf.py`**

Pure functions, no I/O.

`parse_hemispheric_coordinate`: strip, require the last character to be in `NSEW`, take the leading digits, divide by 10 (the ATCF convention is an implicit decimal before the final digit), apply the hemisphere sign. Return `None` otherwise.

`parse_atcf_line`: split on `,`, require at least 10 fields, then read indices **0,1,2,3,4,6,7,8,9,10**. Index 5 (lead time) is read for validation and discarded. Field 2 is `YYYYMMDDHH` → `f"{y}-{m}-{d}T{h}:00:00Z"`. Field 8 is knots → km/h × 1.852. A blank field 8 yields `wind_reported=False`. Return `None` if the timestamp, latitude or longitude does not parse.

`parse_atcf`: split lines, skip `;` header lines and blanks, `parse_atcf_line` each, keep only those whose `basin` field matches (case-insensitive), sort by `iso_time`.

- [ ] **Step 4: Create the fixture from real bytes**

Run: `curl -s https://ftp.nhc.noaa.gov/atcf/archive/2025/aal012025.dat.gz | gunzip | head -6 > tests/fixtures/aal012025_sample.dat`

Keep the file exactly as served. Add a one-line header comment in the test module recording the source URL and fetch date.

- [ ] **Step 5: Run and confirm the parser tests pass**

Run: `venv/bin/python -m pytest tests/test_atcf.py -q`
Expected: PASS

- [ ] **Step 6: Write the failing live-source tests**

```python
async def test_http_403_yields_live_unavailable_not_a_substitute():
    src = AtcfLiveSource(endpoints=("https://example.test/storms.txt",),
                         transport=fake_transport(status=403))
    st = await src.probe()
    assert st.status == "live_unavailable"
    assert st.http_status == 403
    assert st.reason and st.reason != ""
    assert await src.fetch() is None      # NOT a historical record

async def test_http_404_yields_live_unavailable():
    src = AtcfLiveSource(endpoints=("https://example.test/x",),
                         transport=fake_transport(status=404))
    assert (await src.probe()).status == "live_unavailable"

async def test_transport_exception_yields_live_unavailable_with_the_reason():
    src = AtcfLiveSource(endpoints=("https://example.test/x",),
                         transport=fake_transport(raises=TimeoutError("timed out")))
    st = await src.probe()
    assert st.status == "live_unavailable"
    assert "timed out" in st.reason or "timeout" in st.reason.lower()

async def test_200_with_no_active_storm_is_a_distinct_state():
    src = AtcfLiveSource(endpoints=("https://example.test/x",),
                         transport=fake_transport(status=200, body=""))
    st = await src.probe()
    assert st.status == "no_active_storm"
    assert st.reason

async def test_a_non_atcf_body_is_unavailable_not_a_fabricated_storm():
    src = AtcfLiveSource(endpoints=("https://example.test/x",),
                         transport=fake_transport(status=200, body="<html>maintenance</html>"))
    st = await src.probe()
    assert st.status == "live_unavailable"
    assert await src.fetch() is None

async def test_a_real_io_line_becomes_a_record():
    io_line = "IO, 01, 2025062218, 01, TEST, 0, 152N, 845E, 45, 990, TS,\n"
    src = AtcfLiveSource(endpoints=("https://example.test/x",),
                         transport=fake_transport(status=200, body=io_line))
    rec = await src.fetch()
    assert rec is not None
    assert rec.source == "atcf_live"
    assert rec.observed is True
    assert rec.cyclone_id.startswith("IO")

async def test_every_endpoint_is_tried_before_giving_up():
    tried = []
    def t(url):
        tried.append(url)
        return type("R", (), {"status": 403, "text": lambda self: ""})()
    src = AtcfLiveSource(endpoints=("https://a.test", "https://b.test"), transport=t)
    st = await src.probe()
    assert tried == ["https://a.test", "https://b.test"]
    assert st.endpoints == ("https://a.test", "https://b.test")
```

`fake_transport` is a small helper in the test module returning a callable with signature `(url, timeout) -> (status, body)`.

- [ ] **Step 7: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_live_source.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 8: Implement `backend/cyclones/live.py`**

`DEFAULT_ATCF_ENDPOINTS` is the verified list from the ADR, in order: NRL `atcf_web/docs/current_storms.txt`, NRL `atcf_web/current_storms.txt`, NHC `data/atcf/current_storms.txt`, NHC `CurrentStorms.json`. `timeout_s` defaults to 6.0.

`probe()` tries each endpoint in order, recording the URL and the HTTP status. It returns on the first that yields a parseable NI storm; otherwise it returns a `LiveStatus` whose `status` is `no_active_storm` when every reachable endpoint returned an empty body, and `live_unavailable` otherwise. `reason` is composed from the statuses actually observed — e.g. "Tried 4 sources: 403, 403, 404, 200 (no NI cyclone listed)." — never a generic string.

`fetch()` returns a `CycloneRecord` built from `parse_atcf(body, basin="IO")`, with `limitation` stating the positions are operational ATCF fixes, that ATCF wind is a 1-minute mean sustained wind in knots (unlike the 3-minute IMD basis used elsewhere in the app), and that this is a live observation, not a forecast. If nothing parses, `fetch()` returns `None` and the caller reports `live_unavailable`. **There is no code path from `live.py` to `historical.py`.**

- [ ] **Step 9: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_live_source.py -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add backend/cyclones/atcf.py backend/cyclones/live.py \
        tests/test_atcf.py tests/test_live_source.py tests/fixtures/
git commit -m "feat(cyclones): a real ATCF live provider that substitutes nothing"
```

---

## Task 4: Open-Meteo, as a supplementary context layer

Not a cyclone source. Wind and pressure at a point, clearly labelled as model output.

**Files:**
- Create: `backend/weather/__init__.py`, `backend/weather/open_meteo.py`
- Test: `tests/test_open_meteo.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"`
  - `DEFAULT_HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "pressure_msl", "precipitation")`
  - `build_params(latitude: float, longitude: float, forecast_days: int = 2) -> dict[str, str]` — always includes `cell_selection=sea`, `timezone=UTC`, `wind_speed_unit=kmh`, `precipitation_unit=mm`; **never includes `apikey`**
  - `OpenMeteoError(RuntimeError)` with `.status: int | None` and `.reason: str`
  - `class OpenMeteoClient(fetch: Callable[[str], tuple[int, str]] | None = None)` with `def current(self, latitude: float, longitude: float) -> WeatherObservation`
  - `WeatherObservation(latitude, longitude, wind_kmph, gust_kmph, direction_deg, pressure_hpa, precipitation_mm, observed_at, interval_s, model, limitation) -> to_dict()`

- [ ] **Step 1: Write the failing tests**

```python
def test_params_never_include_an_api_key():
    # Verified live: Open-Meteo needs no key, and an INVALID key returns 400.
    p = build_params(21.65, 88.06)
    assert "apikey" not in {k.lower() for k in p}
    assert p["cell_selection"] == "sea"   # the storm is over water
    assert p["timezone"] == "UTC"
    assert p["forecast_days"] == "2"

def test_forecast_days_stays_inside_the_documented_range():
    for d in (0, 1, 7, 16):
        assert build_params(21.65, 88.06, forecast_days=d)["forecast_days"] == str(d)
    with pytest.raises(ValueError):
        build_params(21.65, 88.06, forecast_days=17)

def test_a_400_becomes_a_disclosed_failure_not_empty_weather():
    # Verified live: bad variable / bad latitude / bad date all return 400
    # with {"error": true, "reason": ...}.
    c = OpenMeteoClient(fetch=lambda url: (400, '{"error":true,"reason":"Invalid value"}'))
    with pytest.raises(OpenMeteoError) as e:
        c.current(21.65, 88.06)
    assert e.value.status == 400
    assert e.value.reason == "Invalid value"

def test_knots_are_never_reported_as_a_sustained_wind():
    c = OpenMeteoClient(fetch=lambda url: (200, BODY))
    o = c.current(21.65, 88.06)
    # wind_speed_10m is a 10-min mean; wind_gusts_10m is a preceding-hour max.
    assert o.wind_kmph == 9.2
    assert o.interval_s == 900
    assert "10-minute mean" in o.limitation
    assert "preceding hour" in o.limitation

def test_missing_variable_in_the_payload_is_not_zero():
    body = '{"latitude":21.65,"current":{"time":"2026-10-01T00:00","interval":900,"wind_speed_10m":9.2}}'
    o = OpenMeteoClient(fetch=lambda url: (200, body)).current(21.65, 88.06)
    assert o.gust_kmph is None      # absent, not 0
    assert o.pressure_hpa is None
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_open_meteo.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`build_params` validates `forecast_days` against the documented `0..16` and raises `ValueError` outside it. `latitude`/`longitude` are formatted to 4 dp.

`OpenMeteoClient.current` builds the URL, calls `fetch` (defaulting to `urllib.request` with a 10 s timeout), and on non-200 raises `OpenMeteoError` carrying the parsed `reason` — falling back to the raw body text when the body is not the documented error shape. On 200 it reads `current` and maps fields with a strict `isinstance(x, (int, float))` check so an absent or null field becomes `None`, never `0`.

`limitation` states: model output, not an observation; 10-minute mean wind, gusts are the preceding-hour maximum; the grid cell was selected preferring water (`cell_selection=sea`); an `apikey` is not used because none is required.

- [ ] **Step 4: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_open_meteo.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/weather tests/test_open_meteo.py
git commit -m "feat(weather): Open-Meteo as a disclosed supplementary context layer"
```

---

## Task 5: Scenario context and cache isolation

**The correctness-critical task.** A cache entry for one storm must never answer for another.

**Files:**
- Create: `backend/cyclones/scenarios.py`, `backend/cyclones/registry.py`
- Modify: `backend/main.py` — `surge_for_category` (L280), `flood_for_category` (L285), `populations_for_category` (L1258), `shelters_for_category` (L1297), `allocation_for_category` (L1354)
- Test: `tests/test_scenarios.py`, `tests/test_cache_isolation.py`

**Interfaces:**
- Consumes: `CycloneRecord` from Task 1.
- Produces:
  - `DEFAULT_CYCLONE_ID = "2024145N14087"`
  - `Scenario(scenario_id: str, label: str, wind_kmph: float, kind: str, origin: str) -> Scenario` where `kind` ∈ `{"observed", "category", "band_midpoint"}`
  - `SCENARIO_CATALOGUE: tuple[Scenario, ...]` — `observed` plus one per IMD band, built from the existing `IMD_BANDS` so the numbers cannot drift
  - `ScenarioContext(cyclone_id: str = DEFAULT_CYCLONE_ID, scenario_id: str = "cat6") -> ScenarioContext` with `.wind_kmph`, `.key` (a `(cyclone_id, scenario_id)` tuple), `.cyclone_id`, `.scenario_id`
  - `resolve_wind_kmph(scenario_id: str, cyclone: CycloneRecord | None) -> float | None`
  - `class CycloneRegistry` with `.historical() -> list[CycloneRecord]`, `.get(cyclone_id: str) -> CycloneRecord | None`, `.live_status() -> LiveStatus`

- [ ] **Step 1: Write the failing cache-isolation tests**

`tests/test_cache_isolation.py`, declaring
`REPO_ROOT = Path(__file__).resolve().parents[1]` as above:

```python
def test_two_cyclones_at_the_same_scenario_do_not_share_a_cache_entry():
    # THE regression this task exists to prevent. Both scenarios resolve to the
    # same wind, so a category-keyed cache would serve one storm's flood extent
    # for the other — a confidently wrong map, not an error.
    f = flood_for_scenario(ScenarioContext("2024145N14087", "cat6"))
    g = flood_for_scenario(ScenarioContext("2023001N10070", "cat6"))
    assert f is not g
    assert f.key != g.key

def test_cache_info_shows_the_context_in_the_key():
    flood_for_scenario(ScenarioContext("2024145N14087", "cat6"))
    info = flood_for_scenario.cache_info()
    assert info.currsize >= 1

def test_the_same_context_hits_the_cache():
    flood_for_scenario.cache_clear()
    a = flood_for_scenario(ScenarioContext("2024145N14087", "cat6"))
    b = flood_for_scenario(ScenarioContext("2024145N14087", "cat6"))
    assert a is b

def test_observed_scenario_differs_from_the_category_it_matches():
    # If an observed peak happens to equal a band's wind, they are still
    # different scenarios and must not share an entry.
    obs = ScenarioContext("2024145N14087", "observed")
    assert obs.key != ScenarioContext("2024145N14087", "cat3").key

def test_no_lru_cache_in_main_is_keyed_on_a_bare_category():
    # A guard, not a style rule: the whole bug class is a cache whose key
    # omits the storm.
    src = (REPO_ROOT / "backend" / "main.py").read_text()
    for m in re.finditer(r"@lru_cache\([^)]*\)\s*\ndef\s+(\w+)\(([^)]*)\)", src):
        params = [p.strip().split(":")[0].split("=")[0].strip()
                  for p in m.group(2).split(",") if p.strip()]
        if m.group(1) == "_component_index":
            continue                      # road graph, storm-independent
        assert any(p in ("ctx", "context") or "Context" in p for p in params), \
            f"{m.group(1)} is cached on {params} without a ScenarioContext"

def test_an_unknown_cyclone_id_falls_back_to_remal_and_says_so():
    ctx = ScenarioContext("no-such-storm", "cat6")
    reg = CycloneRegistry()
    assert reg.get("no-such-storm") is None
    # Falling back is allowed for the *default*; it must never be silent.
    assert reg.default_cyclone_id() == DEFAULT_CYCLONE_ID
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_scenarios.py tests/test_cache_isolation.py -q`
Expected: FAIL — the scenario module is missing and `main.py`'s caches are category-keyed

- [ ] **Step 3: Implement `backend/cyclones/scenarios.py`**

`SCENARIO_CATALOGUE` is built by importing `IMD_BANDS` from `backend.simulation.surge` and calling each band's existing representative-wind helper — **no numbers are restated**. The `observed` scenario's wind comes from `peak_wind_kmph` of that cyclone's own waypoints, and is `None` when nothing reported a wind; `resolve_wind_kmph` then raises a `ValueError` naming the cyclone, because a scenario with no wind has no simulation.

Category 6 has no upper bound in IMD's table, so its scenario is labelled `kind="band_midpoint"` only when a midpoint exists and `kind="category"` with `wind_is_band_midpoint=False` otherwise — matching what the existing API already reports.

`ScenarioContext.__init__` validates both ids, raising `KeyError` naming the valid ids, so a typo is a clear error rather than a silent default. `.key` returns `(cyclone_id, scenario_id)`.

- [ ] **Step 4: Implement `backend/cyclones/registry.py`**

`CycloneRegistry` loads `data/cyclones/catalogue.json` once under `lru_cache(maxsize=1)` — it is cyclone-independent, so it legitimately has no `ScenarioContext`. `.live_status()` calls `AtcfLiveSource().probe()` on every call (not cached), because a stale live status is worse than a slow one.

- [ ] **Step 5: Re-key the five caches in `backend/main.py`**

Replace each category-only signature with a context signature. Example:

```python
@lru_cache(maxsize=32)
def flood_for_scenario(ctx: ScenarioContext) -> FloodResult:
    """Flood extent for a (cyclone, scenario) pair. ~6 s of raster work per call."""
    return run_flood_model(ctx.wind_kmph)
```

`surge_for_category` → `surge_for_scenario(ctx) -> SurgeResult`, returning `predict_surge(ctx.wind_kmph)` unchanged. `populations_for_category` and `shelters_for_category` take `ctx` and call `flood_for_scenario(ctx)`. `allocation_for_scenario(ctx)` calls both. `_component_index` stays as it is — the road graph does not depend on the storm, and the guard test skips it explicitly.

Every existing endpoint keeps its behaviour when called with no parameters, by defaulting `ctx` to `ScenarioContext()`.

- [ ] **Step 6: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_scenarios.py tests/test_cache_isolation.py -q`
Expected: PASS

Then run the full suite — **the surge numbers must not move**:
Run: `venv/bin/python -m pytest -q`
Expected: 352 + the new tests, 0 failures. Confirm `GET /exposure?category=6` still reports 12 hospitals, 22 substations, 251 roads and surge 4.47 m.

- [ ] **Step 7: Commit**

```bash
git add backend/cyclones/scenarios.py backend/cyclones/registry.py \
        backend/main.py tests/test_scenarios.py tests/test_cache_isolation.py
git commit -m "fix(cache): key every scenario cache on (cyclone_id, scenario_id)"
```

---

## Task 6: ML storm peak intensity, with a baseline gate

Real features, real target, and an explicit refusal to ship a model that cannot beat the median.

**Files:**
- Create: `backend/ml/__init__.py`, `backend/ml/storm_peak_intensity.py`, `backend/data_pipeline/train_storm_peak_intensity.py`
- Test: `tests/test_storm_peak_intensity.py`

**Interfaces:**
- Consumes: `read_ni_rows`, `ibtracs_number` from Task 2.
- Produces:
  - `FEATURE_NAMES = ("translation_speed_kmph", "bearing_deg", "distance_to_land_km", "latitude", "longitude", "month", "basin_distance_km", "peak_before_kmph")`
  - `TrainingRow(sid: str, name: str, season: int, features: tuple[float, ...], target_kt: float) -> TrainingRow`
  - `build_training_set(since: int = 1970) -> tuple[TrainingRow, ...]`
  - `median_baseline_mae(rows) -> float`
  - `LeaveOneOutReport(n: int, mae_kt: float, baseline_mae_kt: float, r2: float, beats_baseline: bool, per_fold: tuple[float, ...]) -> to_dict()`
  - `evaluate(rows) -> LeaveOneOutReport`
  - `StormPeakEstimate(predicted_kt: float, baseline_kt: float, interval_kt: tuple[float, float], n_training: int, beats_baseline: bool, limitation: str) -> to_dict()`
  - `class StormPeakIntensityModel` with `.fit(rows)`, `.predict(features) -> float`, `.from_json(path)`, `.to_json(path)`
  - `estimate_for(features) -> StormPeakEstimate`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_landfall_column_is_never_treated_as_a_flag():
    # IBTrACS LANDFALL holds DIST2LAND in nautical miles on nearly every NI row
    # (55,995 of 57,852 measured; values 0, 10, 246, 570, 583...). Treating
    # "LANDFALL != ''" as a landfall flag would mislabel the whole training set.
    # The target is the peak USA_WIND over the storm's lifetime, which needs no
    # interpretation of that column at all.
    rows = build_training_set()
    assert rows
    assert all(20.0 <= r.target_kt <= 160.0 for r in rows)

def test_training_set_is_ni_only_and_modern():
    rows = build_training_set()
    assert all(r.season >= 1970 for r in rows)
    assert 250 <= len(rows) <= 400   # measured: 299

def test_features_are_finite_and_within_domain():
    for r in build_training_set():
        assert len(r.features) == len(FEATURE_NAMES)
        assert all(math.isfinite(v) for v in r.features)

def test_remal_is_in_the_training_set_and_consistent_with_imd():
    remal = next(r for r in build_training_set() if r.name == "REMAL" and r.season == 2024)
    # IBTrACS says 60 kt; IMD documented landfall 110-120 km/h = 59-65 kt.
    assert 55 <= remal.target_kt <= 65

def test_the_model_must_beat_the_median_baseline_to_be_reportable():
    rep = evaluate(build_training_set())
    assert rep.n >= 250
    # The gate. If this is False the model is not shipped as a prediction; the
    # baseline is reported with an explanation instead.
    assert rep.beats_baseline == (rep.mae_kt < rep.baseline_mae_kt)

def test_report_states_its_own_limits():
    rep = evaluate(build_training_set())
    d = rep.to_dict()
    assert d["n"] >= 250
    assert d["mae_kt"] > 0
    assert "kt" in d["limitation"]
    assert "leave-one-out" in d["limitation"].lower()

def test_prediction_is_bounded_by_the_training_range():
    m = StormPeakIntensityModel().fit(build_training_set())
    for f in ([0.0] * 8, [1e6] * 8, [float("nan")] * 8):
        p = m.predict(tuple(f))
        assert p is None or 10.0 <= p <= 170.0
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_storm_peak_intensity.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `build_training_set`**

One row per storm, ≥1970, `BASIN == 'NI'`.

**Target**: `max(USA_WIND)` over all the storm's NI rows where `USA_WIND ≥ 0` — measured 20–150 kt, median 50 kt.

**Naming, and why it is "storm peak intensity" and not "landfall intensity".** The
target is the peak sustained wind the storm attained *anywhere in its lifetime*.
It is **not** an intensity at a landfall, and calling it one would be a
misdescription of what the number is — the same class of error as the
"trained regression" wording corrected in item 10 of the previous round. So the
component is named `storm_peak_intensity` throughout: module, class, test file,
artefact and API field.

A genuine landfall target would need a **landfall window** — the set of fixes
surrounding the moment of landfall — and IBTrACS does not provide one that can
be defined rigorously from the fields present. `LANDFALL` is not a flag (see
below) and `DIST2LAND` alone cannot establish *which* crossing is the landfall
of interest: a Bay of Bengal storm typically crosses land several times, and
Sagar Island is not necessarily the first. Deriving that would need either a
coastline-distance join or an agency landfall time, neither of which is in this
dataset. **Do not derive one from `LANDFALL != ''`.** If a rigorous
landfall-window target is wanted later, it is a separate piece of work with its
own validation, and the honest interim answer is this name.

**Features**, all available on 100% of the measured target rows:

| Feature | Source | Transform |
|---|---|---|
| `translation_speed_kmph` | `STORM_SPEED` | knots → km/h × 1.852 |
| `bearing_deg` | `STORM_DIR` | degrees, 0–360 |
| `distance_to_land_km` | `DIST2LAND` | nautical miles → km × 1.852 |
| `latitude` | `LAT` | at the storm's peak-wind fix |
| `longitude` | `LON` | at the storm's peak-wind fix |
| `month` | `ISO_TIME` | 1–12 |
| `basin_distance_km` | derived | great-circle km from the study-area centroid (21.95 N, 88.05 E) |
| `peak_before_kmph` | derived | peak `USA_WIND` strictly *before* the peak-wind fix, in km/h |

A storm with no prior peak gets `peak_before_kmph = 0.0` and the model is fit with that value present, so the shape never varies.

**Model**: `sklearn.linear_model.RidgeCV` over `StandardScaler` output. Ridge with a cross-validated penalty rather than plain OLS, because with 299 rows and 8 correlated features an unpenalised fit would chase the fold noise. Import from the existing scikit-learn already in `venv`.

**Evaluation**: leave-one-out CV, 299 folds, reporting MAE in knots, the median-baseline MAE for comparison, and `r2`. `beats_baseline` is `mae_kt < baseline_mae_kt`.

- [ ] **Step 4: Implement the gate in `estimate_for`**

If `beats_baseline` is false, `StormPeakEstimate.predicted_kt` is still returned but `beats_baseline=False` and the `limitation` says the model does not beat a flat median on held-out storms, so the number must not be presented as a prediction. The API surfaces that flag; the UI must not label the number "predicted" when it is false.

`limitation` states: n, leave-one-out MAE, the baseline MAE, that this is an ML estimate and is **not** the surge figure, that the surge figure comes from the deterministic law and remains authoritative, that `USA_WIND` is a 1-minute mean sustained wind in knots while the app's IMD bands are 3-minute, and that the training set is 33% at-or-above 64 kt.

- [ ] **Step 5: Implement the training CLI**

`backend/data_pipeline/train_storm_peak_intensity.py` writes `data/ml/storm_peak_intensity.json` containing the fitted coefficients, the scaler mean and scale, the LOOCV report, and the git SHA. Deterministic: no random seed is used, and re-running on unchanged input produces a byte-identical file.

- [ ] **Step 6: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_storm_peak_intensity.py -q`
Expected: PASS

- [ ] **Step 7: Record the honest result in MEMORY.md**

Whatever the numbers are, append a "Flagged for review" entry stating the measured n, the LOOCV MAE, the baseline MAE, whether the gate passed, and — if it failed — that the shipped figure is the baseline and why. **Do not adjust the model to make the gate pass.** A failed gate reported honestly is the correct outcome for n=299.

- [ ] **Step 8: Commit**

```bash
git add backend/ml backend/data_pipeline/train_storm_peak_intensity.py \
        tests/test_storm_peak_intensity.py data/ml/ MEMORY.md
git commit -m "feat(ml): storm peak intensity from real IBTrACS features, gated on beating the baseline"
```

---

## Task 7: API surface

New endpoints, optional parameters on existing ones, self-describing responses.

**Files:**
- Modify: `backend/main.py` — add `GET /cyclones`, `GET /cyclones/{id}/track`, `GET /scenarios`, `GET /live-cyclone`, `GET /comparison`, `GET /risk-analyst`; add optional `cyclone_id` / `scenario_id` to the existing endpoints
- Test: `tests/test_new_endpoints.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: the endpoint and response shapes below.

- [ ] **Step 1: Write the failing endpoint tests**

```python
def test_cyclones_lists_historical_and_states_its_source():
    r = client.get("/cyclones")
    assert r.status_code == 200
    b = r.json()
    assert b["source"]["dataset"] == "IBTrACS v04r01"
    assert b["source"]["basin_filter"] == "NI"
    assert b["limitation"]
    ids = [c["cyclone_id"] for c in b["cyclones"]]
    assert "2024145N14087" in ids
    assert ids[0] == "2024145N14087"      # Remal stays the default

def test_live_cyclone_reports_live_unavailable_rather_than_substituting():
    r = client.get("/live-cyclone")
    assert r.status_code == 200            # a state, not an error
    b = r.json()
    assert b["status"] in ("available", "live_unavailable", "no_active_storm")
    assert b["checked_at"]
    assert b["reason"]
    assert b["source"]
    if b["status"] != "available":
        assert b["cyclone"] is None        # never a historical stand-in
        assert "historical" in b["reason"].lower()

def test_existing_endpoints_still_work_with_no_parameters():
    for path in ("/categories", "/track", "/exposure?category=6", "/routes",
                 "/allocation", "/surge-zone?category=6"):
        assert client.get(path).status_code == 200

def test_exposure_defaults_are_byte_identical_after_the_rekey():
    a = client.get("/exposure?category=6").json()
    assert a["hospitals"]["count"] == 12
    assert a["substations"]["count"] == 22
    assert a["roads_cut_off"]["count"] == 251
    assert a["surge"]["surge_m"] == pytest.approx(4.4719, abs=1e-4)

def test_a_second_cyclone_at_the_same_scenario_is_served_not_cached():
    a = client.get("/exposure?category=6&cyclone_id=2024145N14087").json()
    b = client.get("/exposure?category=6&cyclone_id=2023001N10070").json()
    assert a["cyclone"]["cyclone_id"] == "2024145N14087"
    assert b["cyclone"]["cyclone_id"] == "2023001N10070"

def test_an_unknown_scenario_is_a_400_naming_the_valid_ids():
    r = client.get("/exposure?category=6&scenario_id=nope")
    assert r.status_code == 400
    assert "cat6" in r.json()["detail"]

def test_every_response_carries_its_provenance():
    for path in ("/exposure?category=6", "/cyclones", "/scenarios",
                 "/live-cyclone", "/comparison?category=6"):
        b = client.get(path).json()
        assert "limitation" in b, path
        assert "generated_at" in b, path
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_new_endpoints.py -q`
Expected: FAIL — 404s on the new paths

- [ ] **Step 3: Add the endpoints**

`GET /cyclones` → `{"source": {...}, "generated_at": ..., "limitation": ..., "cyclones": [...]}` with each entry carrying `cyclone_id`, `name`, `season`, `basin`, `subbasin`, `peak_wind_kmph`, `waypoint_count`, `is_case_study`. Sorted so Remal is first, then by season descending.

`GET /cyclones/{cyclone_id}/track` → the same shape `/track` returns today, so the existing map code can consume either, plus a `cyclone` block.

`GET /live-cyclone` → `{"status": ..., "source": ..., "http_status": ..., "reason": ..., "checked_at": ..., "endpoints": [...], "cyclone": None | {...}, "limitation": ...}`. **Never** 5xx for an unreachable source; that is a state, not an error.

`GET /scenarios` → `{"cyclone_id": ..., "scenarios": [...], "limitation": ...}`.

`GET /comparison?category=&cyclone_ids=a,b` → per-cyclone, per-scenario figures and the deltas between them.

Add `cyclone_id: str = DEFAULT_CYCLONE_ID` and `scenario_id: str = "cat6"` as optional query parameters to `/exposure`, `/surge-zone`, `/routes`, `/allocation` and `/track`. `category` stays and keeps working: when `scenario_id` is given it wins, and the response states which was used.

- [ ] **Step 4: Add provenance to every response**

One helper `_provenance(ctx) -> dict` returning `cyclone_id`, `scenario_id`, `wind_kmph`, `method`, `generated_at` and `limitation`. Merged into every response. The existing `_category_header` already carries `method`/`anchor`/`limitation` — reuse it rather than adding a parallel one.

- [ ] **Step 5: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_new_endpoints.py -q`
Expected: PASS
Then: `venv/bin/python -m pytest -q` — 0 failures, no existing test weakened.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py tests/test_new_endpoints.py
git commit -m "feat(api): cyclone and scenario addressing, with provenance on every response"
```

---

## Task 8: The AI Risk Analyst

Evidence-grounded, behind a deliberate press, and unable to invent a number.

**Files:**
- Modify: `backend/ai/advisory.py` — add `RiskAnalysis` schema, `build_risk_prompt`, `generate_risk_analysis`
- Modify: `backend/main.py` — `POST /risk-analyst`
- Test: `tests/test_risk_analyst.py`

**Interfaces:**
- Consumes: Task 5's `ScenarioContext`, Task 6's `StormPeakEstimate`, Task 7's comparison payload.
- Produces:
  - `class RiskFinding(BaseModel)`: `finding: str`, `evidence: str`, `severity: Literal["CRITICAL","HIGH","MEDIUM","LOW"]`, `evidence_kind: Literal["computed","historical","model_estimate","general_knowledge"]`
  - `class RiskAnalysis(BaseModel)`: `summary: str`, `findings: list[RiskFinding]`, `comparison_note: str`, `disclaimer: str`
  - `def build_risk_prompt(...) -> str`
  - `async def generate_risk_analysis(...) -> RiskAnalysis` — pinned `ADVISORY_MODEL`, no fallback, no rotation
  - `POST /risk-analyst` body `{"category": int, "cyclone_id": str, "scenario_id": str, "origin": str | None}`

- [ ] **Step 1: Write the failing tests**

`tests/test_risk_analyst.py`, declaring
`REPO_ROOT = Path(__file__).resolve().parents[1]` as above:

```python
def test_every_finding_must_cite_evidence():
    f = RiskFinding(finding="Substation cut off", evidence="12 substations exposed",
                    severity="HIGH", evidence_kind="computed")
    assert f.evidence
    assert f.evidence_kind in ("computed","historical","model_estimate","general_knowledge")

def test_the_prompt_labels_the_ml_estimate_separately_from_the_surge():
    p = build_risk_prompt(context=ctx, exposure=exposure, comparison=cmp,
                          peak_estimate=est, advisory=None)
    assert "1.2" in p or "anchored quadratic" in p
    ml = p[p.index("MODEL ESTIMATE"):p.index("MODEL ESTIMATE")+400]
    assert "ML" in ml and "not" in ml
    assert "authoritative" in p

def test_the_prompt_forbids_inventing_numbers():
    p = build_risk_prompt(context=ctx, exposure=exposure, comparison=cmp,
                          peak_estimate=est, advisory=None)
    assert "only the numbers" in p.lower() or "do not" in p.lower()

def test_the_prompt_requires_the_disclaimer_and_no_official_claim():
    p = build_risk_prompt(context=ctx, exposure=exposure, comparison=cmp,
                          peak_estimate=est, advisory=None)
    assert "not an official warning" in p.lower()
    assert "IMD" in p

def test_the_model_string_is_pinned_with_no_fallback():
    src = (REPO_ROOT / "backend" / "ai" / "advisory.py").read_text()
    assert 'ADVISORY_MODEL = "gemini-3.8-flash"' in src
    assert "except" not in src[src.index("async def generate_risk_analysis"):][:400]

async def test_quota_exhaustion_surfaces_the_existing_error_path():
    # Reuses describeAdvisoryError's taxonomy; must not invent a new kind.
    with pytest.raises(ApiError) as e:
        await generate_risk_analysis(context=ctx, exposure=exposure, ...)
    assert e.value.kind in ("quota", "unavailable", "config", "network")
```

- [ ] **Step 2: Run and confirm it fails**

Run: `venv/bin/python -m pytest tests/test_risk_analyst.py -q`
Expected: FAIL

- [ ] **Step 3: Implement the schema and prompt**

`build_risk_prompt` assembles, in clearly separated blocks:

- `COMPUTED FIGURES` — only numbers the app computed: exposed counts, flooded area, surge from the deterministic law, route length, allocation.
- `MODEL ESTIMATE` — the ML storm-peak figure, with its LOOCV MAE, its baseline MAE, whether it beat the baseline, and the words "ML estimate, not a measurement" and "not the surge figure".
- `COMPARISON` — the per-scenario deltas, each labelled with the two scenarios it compares.
- `HISTORICAL CONTEXT` — the same verified pool `/advisory` already uses; nothing new.
- Rules: use only these numbers, never invent one; do not claim to be an official warning; do not speak as IMD; carry every disclaimer; state clearly that the deterministic surge law remains authoritative and the ML figure does not change it.

- [ ] **Step 4: Implement the call**

`generate_risk_analysis` reuses the existing capacity ladder and `ApiError` taxonomy verbatim. **No model fallback, no rotation** — `Rules.md` forbids a silent swap. It is called only from an explicit `POST`, never from a slider or a chip press.

- [ ] **Step 5: Run and confirm it passes**

Run: `venv/bin/python -m pytest tests/test_risk_analyst.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/ai/advisory.py backend/main.py tests/test_risk_analyst.py
git commit -m "feat(ai): an evidence-grounded AI Risk Analyst, pinned model, no fallback"
```

---

## Task 9: Frontend state and API client

**Files:**
- Create: `mobile/cycloneModel.ts`, `mobile/apiCyclones.ts`
- Modify: `mobile/api.ts`
- Test: `mobile/tests/cycloneModel.test.mjs`

**Interfaces:**
- Consumes: Task 7's response shapes.
- Produces:
  - `Cyclone`, `ScenarioSummary`, `LiveCycloneState`, `ComparisonRow`, `RiskAnalysis` types
  - `liveStateLabel(state: LiveCycloneState) -> string` — `"Live"`, `"No active cyclone"`, or `"Live feed unavailable"`; **never** a string implying a live storm exists when it does not
  - `freshnessNote(state) -> string` — includes `checked_at`, or a "never checked" when absent
  - `comparisonDeltas(rows) -> ComparisonRow[]`
  - `apiCyclones.ts`: `getCyclones()`, `getCycloneTrack(id)`, `getScenarios(id)`, `getLiveCyclone()`, `getComparison(ids, category)`, `postRiskAnalysis(body)`

- [ ] **Step 1: Write the failing tests**

```javascript
test('an unavailable live feed never says a storm is happening', () => {
  const s = { status: 'live_unavailable', reason: 'Tried 4 sources: 403, 403, 404, 200.',
              source: 'nrlmry.navy.mil', http_status: 403,
              checked_at: '2026-10-01T00:00:00Z', cyclone: null };
  const label = liveStateLabel(s);
  assert.match(label, /unavailable/i);
  assert.doesNotMatch(label, /live storm|active cyclone|watch/i);
});

test('no_active_storm is distinct from unavailable', () => {
  assert.notStrictEqual(
    liveStateLabel({ status: 'no_active_storm' }),
    liveStateLabel({ status: 'live_unavailable' }),
  );
});

test('freshness always carries the timestamp it was checked', () => {
  assert.match(
    freshnessNote({ status: 'live_unavailable', checked_at: '2026-10-01T09:30:00Z' }),
    /09:30/,
  );
  assert.match(freshnessNote({ status: 'live_unavailable', checked_at: null }), /never checked/i);
});

test('an ml estimate is never labelled a prediction when it lost to the baseline', () => {
  assert.doesNotMatch(mlEstimateLabel({ beats_baseline: false }), /predicted/i);
  assert.match(mlEstimateLabel({ beats_baseline: false }), /median/i);
});

test('deltas name the two scenarios being compared', () => {
  const rows = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'cat4', surge_m: 1.83, exposed: 0 },
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137 },
  ]);
  assert.equal(rows[0].label, 'cat4 → cat5');
  assert.equal(rows[0].surge_delta_m, 1.58);
});
```

- [ ] **Step 2: Run and confirm it fails**

Run: `cd mobile && node --test tests/cycloneModel.test.mjs`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

`cycloneModel.ts` holds the types and the pure label/format functions. The doc comment on `liveStateLabel` records the rule in prose: an unavailable feed must never read as an active storm, because a judge seeing "Live feed unavailable" should conclude nothing is being claimed, not that something is.

`apiCyclones.ts` follows `api.ts`'s existing `ApiError` taxonomy and `READ_TIMEOUT_MS` (150 s). `mobile/api.ts` gains optional `cycloneId` / `scenarioId` arguments on `getExposure`, `getRoutes`, `getAllocation`, `getTrack`, all defaulting to `undefined` so the existing call sites are unchanged.

- [ ] **Step 4: Run and confirm it passes**

Run: `cd mobile && node --test 'tests/*.test.mjs'` → PASS, 291 + the new tests
Then: `cd mobile && npx tsc --noEmit` → exit 0

- [ ] **Step 5: Commit**

```bash
git add mobile/cycloneModel.ts mobile/apiCyclones.ts mobile/api.ts \
        mobile/tests/cycloneModel.test.mjs
git commit -m "feat(mobile): cyclone state, live-state labelling and the comparison maths"
```

---

## Task 10: Wire the UI on both platforms

Map-centred, existing primitives, no new tokens.

**Files:**
- Create: `mobile/components/CyclonePicker.tsx`, `mobile/components/ScenarioComparePanel.tsx`, `mobile/components/RiskAnalystPanel.tsx`
- Modify: `mobile/components/MapScreen.tsx`, `mobile/components/MapScreen.web.tsx`
- Test: `mobile/tests/scenarioCompare.test.mjs`, and additions to `mobile/tests/webBundleSafety.test.mjs`

**Interfaces:**
- Consumes: Task 9's client and labels.
- Produces: three components and the wiring in both map screens.

- [ ] **Step 1: Write the failing comparison tests**

```javascript
test('a zero flooded area is shown as zero, not as missing', () => {
  assert.match(exposureText({ flooded_km2: 0 }), /0/);
  assert.doesNotMatch(exposureText({ flooded_km2: 0 }), /—|unknown|undefined/);
});

test('an unavailable live feed renders the reason and never a storm name', () => {
  const t = liveBannerText({ status: 'live_unavailable', reason: '403 from every source.',
                             checked_at: '2026-10-01T00:00:00Z', cyclone: null });
  assert.match(t, /403/);
  assert.doesNotMatch(t, /Remal|Cyclone [A-Z]/);
});

test('the comparison sheet always shows the surge method', () => {
  assert.match(comparisonFooterText(), /anchored quadratic scaling/i);
});
```

- [ ] **Step 2: Run and confirm it fails**

Run: `cd mobile && node --test tests/scenarioCompare.test.mjs`
Expected: FAIL

- [ ] **Step 3: Implement `mobile/scenarioCompare.ts`**

`comparisonDeltas`, `exposureText`, `liveBannerText`, `comparisonFooterText`, `mlEstimateLabel`. The footer always states the surge method name, because a comparison table of ML-looking numbers without it invites the exact confusion the architecture is built to prevent.

- [ ] **Step 4: Implement the three components**

`CyclonePicker` — a `GhostButton` row of the cyclone list plus a Historical / Live toggle. Selecting Live shows the live banner: available → the storm and its `checked_at`; unavailable → `liveBannerText`, which never contains a storm name.

`ScenarioComparePanel` — the comparison sheet. Opens from a `GhostButton` in the existing panel, never as a route. Every row labelled with both scenarios compared; a zero flooded area shown as `0`; the footer carrying the surge method.

`RiskAnalystPanel` — opens only from an explicit `Generate analysis` button, mirroring the existing advisory gate. Shows findings with an evidence chip per finding, distinguishing `computed` / `model_estimate` / `historical` / `general_knowledge`. An `ML estimate` row is labelled by `mlEstimateLabel`, so a baseline-losing model reads "median baseline", never "predicted".

Both components use only `theme.ts` tokens and the existing primitives. **No `StyleSheet` entry introduces a new colour.**

- [ ] **Step 5: Wire both map screens**

`MapScreen.tsx` and `MapScreen.web.tsx` each gain: cyclone state, scenario state replacing the fixed `chipId` default, `CyclonePicker` above the existing `StrengthChips`, `ScenarioComparePanel` and `RiskAnalystPanel` reached from the existing panel. The existing chips, tiles, legend, controls, origin picker and advisory flow are **unchanged in behaviour** — the scenario chips become a superset, with `cat4/5/6` still present and still defaulting as today.

Keep the Leaflet map centred: the cyclone picker changes the track and the scenario, not the layout.

- [ ] **Step 6: Extend the bundle-safety test**

Add to `WEB_GRAPH`: `cycloneModel.ts`, `scenarioCompare.ts`, `apiCyclones.ts`, `components/CyclonePicker.tsx`, `components/ScenarioComparePanel.tsx`, `components/RiskAnalystPanel.tsx` — so none can import a native module. Keep the existing assertions, including that `MapScreen.tsx` renders `LeafletMap` and imports no `react-native-maps`.

- [ ] **Step 7: Run and confirm everything passes**

Run: `cd mobile && node --test 'tests/*.test.mjs'` → 0 failures
Run: `cd mobile && npx tsc --noEmit` → exit 0
Run: `EXPO_PUBLIC_API_URL=https://cyclone-forecaster-chi.vercel.app npx expo export --platform web --output-dir dist --clear`
Then assert the exported bundle contains none of `codegenNativeComponent`, `RNMapView`, `RNMaps`, `AIRMap`, `RNCWebView`.

- [ ] **Step 8: Verify in a browser against the real backend**

Export, serve locally, drive with headless Chrome CDP, and confirm: Remal is the default; switching to a second historical cyclone redraws the track and the counts change; the Live toggle shows the unavailable state with a reason and no storm name; the comparison sheet shows two scenarios and the surge method; the badge contrast regression (MEMORY.md §50, marker count) has not returned; the console is error-free.

- [ ] **Step 9: Commit**

```bash
git add mobile/scenarioCompare.ts \
        mobile/components/CyclonePicker.tsx \
        mobile/components/ScenarioComparePanel.tsx \
        mobile/components/RiskAnalystPanel.tsx \
        mobile/components/MapScreen.tsx \
        mobile/components/MapScreen.web.tsx \
        mobile/tests/scenarioCompare.test.mjs \
        mobile/tests/webBundleSafety.test.mjs
git commit -m "feat(ui): cyclone picker, scenario comparison and the risk analyst on both platforms"
```

Named paths rather than whole directories: the working tree has untracked files
that are not this task's, and `git add mobile/components` would sweep them in.

---

## Task 11: Documentation, and an honest final verification

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `MEMORY.md`, `Design.md`, `Rules.md`

**Interfaces:**
- Consumes: the measured output of every task above — the ML report, the live
  endpoint statuses, the catalogue size, the test totals.
- Produces: no code. This task's deliverable is that the documentation and the
  running system agree, and that every number in the docs is one that was
  measured rather than one that was hoped for.

- [ ] **Step 1: Update the docs to match what shipped**

Every claim below must be true *as measured*, not as intended:

- The surge law is unchanged and authoritative; the ML figure is a separate, clearly labelled estimate that does not feed it.
- The live provider's default endpoint list, and that all of them currently fail, with the measured statuses.
- `live_unavailable` is the expected default state today, not an edge case.
- The ML model's measured n, LOOCV MAE, baseline MAE, and whether the gate passed.
- IBTrACS is filtered to `BASIN == 'NI'`; `LANDFALL` is a distance in nautical miles, not a flag.
- Open-Meteo's documented parameter ranges and its 400-on-invalid behaviour.

If the ML gate failed, say so in `README.md` in the same place the surge law is described. **Do not move the failed result to a footnote.**

- [ ] **Step 2: Run the full suite, unedited, and paste the output**

```bash
venv/bin/python -m pytest -q
cd mobile && node --test 'tests/*.test.mjs'
cd mobile && npx tsc --noEmit
```

Expected: 0 failures on all three. Existing tests unchanged in count-or-weaker terms — if any test had to be edited rather than added, that is a red flag to investigate, not to accept.

- [ ] **Step 3: Confirm Remal end-to-end and state what was not verified**

`GET /exposure?category=6` → 12 hospitals, 22 substations, 251 roads, surge 4.47 m. `GET /live-cyclone` → a `live_unavailable` state with a reason. `GET /cyclones` → Remal first, NI only.

Then state plainly what was **not** verified: device rendering of the Leaflet map (MEMORY.md §49), the Gemini success path if the free tier is still exhausted (§48), and any ATCF source other than the NHC Atlantic bytes used as a fixture.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md MEMORY.md Design.md Rules.md
git commit -m "docs: record the measured state of the dynamic cyclone system"
```

---

## What This Plan Does Not Build

Stated so it is not mistaken for an oversight.

- **No cyclone forecasting.** Nothing here predicts where a storm will go. The ML layer predicts storm *peak intensity* from historical IBTrACS features; that is a statistical estimate about past storms, not a forecast about a future one.
- **No surge ML.** The surge law stays arithmetic. The user forbade synthesising surge labels, and no source in this repo has a traceable one.
- **No NI-basin ATCF mirror.** None exists publicly today. The parser is real and tested against real bytes so it works the day one opens, and the endpoint list is configurable so nothing needs a code change then.
- **No new map library on Web, no native map change.** Web keeps its SVG map; native keeps `LeafletMap`.
- **No auth, no location permission, no persisted history, no new dependency on a paid service.**