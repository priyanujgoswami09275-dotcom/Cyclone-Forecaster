# I1–I6 Review Defects Implementation Plan

> **For agentic workers:** This plan is executed inline (executing-plans). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** close the six open Important review items (MEMORY.md:2429), one commit each, no behaviour change outside the named defects.

**Architecture:** four backend fixes (`live.py`/`base.py` reason + id, `advisory.py`/`main.py` gate wording + prompt), one prompt-format fix in `advisory.py`, one one-line mobile fix in `MapScreen.web.tsx`. No endpoint, schema, or field renames.

**Tech Stack:** FastAPI + pytest, React Native web + node --test, tsc.

**Spec:** MEMORY.md:2429–2436 + user decisions from the planning turn:
- I2 = both: prompt blocks not delimited, and C1 scoping re-verified.
- I3 = prompt lacks the storm identity (no response enrichment).
- I5 = the two backend sentences; mobile `comparisonFooterText` left unchanged (no runtime estimate to read).
- I6 = ATCF storm number from field 1; honest `None` record when the feed stops carrying it.

## Global Constraints

- One commit per task; never weaken an existing test (extend, never relax).
- Never touch `.env`/API keys, `run_flood_model`, `n_steps`, `simulate_flood_propagation`, flood test expectations, `data/cyclones/catalogue.json` (md5 `f4437321…`).
- Surge disclosure strings stay verbatim; model `gemini-3.8-flash`; live Gemini stays NOT VERIFIED (stubs only).
- Gates at the end: `venv/bin/python -m pytest -q` (clear `__pycache__` first), `cd mobile && node --test 'tests/*.test.mjs'`, `npx tsc --noEmit`.
- Test doubles patching `generate_advisory` must accept the new `cyclone=` kwarg (Task 6) — grep `def generate(` first.

## Review Focus

1. `/track` byte-identity breaks if `storm_number` leaks into IBTrACS waypoint JSON — `to_dict()` omits it when `None`; a task-4 test pins an IBTRACS waypoint dict.
2. Source guards pass vacuously (Task 1) — mutation-verify: delete the fixed line, watch the test fail, restore.
3. Gate wording rots one branch at a time (Task 3) — both `beats_baseline` branches asserted.
4. Patch-signature breakage (Task 6) — full suite is the net, the grep is the warning.
5. `no_active_storm` reason (Task 2) must satisfy `test_live_source.py:128,164` and `test_cyclones_base.py`; unavailable path keeps `LIVE_UNAVAILABLE_REASON`.

---

### Task 1: I1 — Web `exposureFailed` never raised

**Files:** Modify `mobile/components/MapScreen.web.tsx:344-346`; Test `mobile/tests/exposureFailed.test.mjs` (new).

- [ ] Failing test: source guard (liveLoading.test.mjs style) — exposure effect's `.catch` contains `setExposureFailed(true)`; render chain orders `exposureFailed ?` before `exposureLoading ?`; `setExposureFailed(false)` on the setup path.
- [ ] Run it → FAIL (no `setExposureFailed(true)`).
- [ ] Implement: add it beside `setExposure(null)` in the `if (!cancelled)` guard.
- [ ] Run → PASS; mutation check; commit `I1: raise exposureFailed when the exposure request fails`.

### Task 2: I4 — `no_active_storm` reason claims the feed was unreachable

**Files:** Modify `backend/cyclones/base.py`, `backend/cyclones/live.py:259-266`; Test extend `tests/test_live_source.py`.

- [ ] Failing tests: (a) 200-empty probe → `status == "no_active_storm"`, reason has no `"could not be reached"`, has timestamp + no-substitution promise + detail; (b) raising transport → unavailable reason still starts with `LIVE_UNAVAILABLE_REASON`.
- [ ] Implement: `NO_ACTIVE_STORM_REASON` + `no_active_storm_reason(checked_at)` in `base.py` (same four-claim shape; same timestamp guard); `live.py` branches on `all_spoke`; fix `probe()` docstring.
- [ ] PASS; `pytest tests/test_live_source.py tests/test_cyclones_base.py` green; commit `I4: no_active_storm must not claim the feed was unreachable`.

### Task 3: I5 — two hardcoded gate verdicts

**Files:** Modify `backend/ai/advisory.py:439-446`, `backend/main.py:2110-2119`; Test extend `tests/test_risk_analyst.py`.

- [ ] Failing tests: `beats_baseline=True` → neither backend string contains `"did NOT beat"`/`"did not beat"`; `False` → both current sentences verbatim; existing honesty asserts untouched.
- [ ] Implement: derive both from `beats_baseline`/`estimate_source`/`is_a_prediction`.
- [ ] PASS; full suite; commit `I5: derive the gate verdict from the artefact, don't hardcode it`.

### Task 4: I6 — fabricated live record id

**Files:** Modify `backend/cyclones/base.py:144-192`, `backend/cyclones/atcf.py:156-190`, `backend/cyclones/live.py:172-190`; Test extend ATCF/live tests.

- [ ] Failing tests: (a) fixture line → waypoint carries `storm_number == "01"`; (b) `_record` → `cyclone_id == "IO012025"`-style, never contains `"-live-"`; (c) IBTRACS `to_dict()` has no `storm_number` key; (d) unparseable number → no record.
- [ ] Implement: trailing `storm_number: str | None = None`; parse `fields[1]`; `to_dict()` omits when None; `_record` id via `atcf_storm_id([TARGET_BASIN, f"{number}{year}"])`, `None` when absent; name = id.
- [ ] PASS; suite incl. `/track` byte-identity; commit `I6: derive the live record id from the ATCF storm number`.

### Task 5: I2 — prompt context mixing (+ re-verify C1 scoping)

**Files:** Modify `backend/ai/advisory.py:132-161`; Tests: prompt test + extend `test_advisory_scopes_to_the_request_not_the_default_band`.

- [ ] Failing tests: prompt contains `=== REQUESTING LOCALITY ===` before the origin block and a district header before counts; captured exposure/allocation payloads carry the request's cyclone/scenario.
- [ ] Implement: `===` block headers mirroring `build_risk_prompt`.
- [ ] PASS; suite; commit `I2: delimit the advisory prompt's blocks; pin advisory payload scoping`.

### Task 6: I3 — the prompt never names the storm

**Files:** Modify `backend/ai/advisory.py`, `backend/main.py:1808-1855`; Tests: prompt test + patched doubles in `test_module_c.py`.

- [ ] Failing tests: `build_prompt(..., cyclone={...})` emits `=== STORM AND SCENARIO ===` with name/season/id/scenario; without `cyclone` → no header; patched `generate_advisory` receives the request's `_cyclone_block`.
- [ ] Implement: optional `cyclone: dict | None = None` on `build_prompt`/`generate_advisory`; `main.py` passes `_cyclone_block(...)`; update test doubles.
- [ ] PASS; full 3-suite gates; commit `I3: tell Gemini which storm the advisory is for`.
