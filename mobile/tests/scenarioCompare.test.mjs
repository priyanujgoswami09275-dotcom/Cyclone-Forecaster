/**
 * The comparison sheet and the live banner: the strings a judge reads.
 *
 *   cd mobile && node --test 'tests/scenarioCompare.test.mjs'
 *
 * Three functions, three distinctions that a UI can silently collapse:
 *
 *   1. `exposureText` — a zero is a measurement, not a gap. Printing "—" or
 *      "unknown" for a modelled zero would tell a reader the model did not
 *      answer, when it answered "nothing is flooded".
 *   2. `liveBannerText` — an unavailable feed renders its reason and **never a
 *      storm name**. A name on a screen labelled "unavailable" is a historical
 *      cyclone presented as now, which is the one unforgivable substitution.
 *   3. `comparisonFooterText` — the surge method is always named. A table of
 *      ML-looking numbers without the method that produced them invites exactly
 *      the confusion the architecture is built to prevent.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  comparisonFooterText,
  exposureText,
  liveBannerText,
  pickSecondScenario,
  scenarioForChip,
} from '../scenarioCompare.ts';

test('a zero flooded area is shown as zero, not as missing', () => {
  assert.match(exposureText({ flooded_km2: 0 }), /0/);
  assert.doesNotMatch(exposureText({ flooded_km2: 0 }), /—|unknown|undefined/);
});

test('an unavailable live feed renders the reason and never a storm name', () => {
  const t = liveBannerText({
    status: 'live_unavailable',
    reason: '403 from every source.',
    checked_at: '2026-10-01T00:00:00Z',
    cyclone: null,
  });
  assert.match(t, /403/);
  assert.doesNotMatch(t, /Remal|Cyclone [A-Z]/);
});

test('the comparison sheet always shows the surge method', () => {
  assert.match(comparisonFooterText(), /anchored quadratic scaling/i);
});

// ---------------------------------------------------------------------------
// Choosing the second scenario to compare against.
//
// This exists because the backend's `observed` scenario is **only registered
// for cyclones whose track is committed locally** — `/scenarios?cyclone_id=…`
// lists `observed` for Remal and not for the other 609. Requesting
// `scenario_id=observed` for one of them is a 400:
//   "unknown scenario 'observed'. Available: cat0 … cat6"
// A comparison sheet that offers a button which errors for every storm except
// the case study is worse than no comparison sheet.
// ---------------------------------------------------------------------------

test('a chip maps to its scenario id, and the observed chip to `observed`', () => {
  assert.equal(scenarioForChip('cat6'), 'cat6');
  assert.equal(scenarioForChip('cat4'), 'cat4');
  assert.equal(scenarioForChip('remal_observed'), 'observed');
});

test("the storm's own observed intensity is preferred over any band", () => {
  // The most informative contrast: "what the band says" against "what it
  // actually did".
  const available = ['observed', 'cat0', 'cat5', 'cat6'];
  assert.equal(pickSecondScenario('cat5', available), 'observed');
});

test('a band compares against the nearest other band, not cat0', () => {
  // Without this, `available[0]` is cat0 — a leap across the whole scale whose
  // delta a reader cannot attribute to anything.
  const bands = ['cat0', 'cat1', 'cat2', 'cat3', 'cat4', 'cat5', 'cat6'];
  assert.equal(pickSecondScenario('cat4', bands), 'cat5');
  assert.equal(pickSecondScenario('cat6', bands), 'cat5');
  assert.equal(pickSecondScenario('cat0', bands), 'cat1');
});

test('the observed chip falls back to the strongest band available', () => {
  const bands = ['cat0', 'cat1', 'cat2', 'cat3', 'cat4', 'cat5', 'cat6'];
  assert.equal(pickSecondScenario('observed', bands), 'cat6');
});

test('a cyclone with only one scenario yields no second scenario', () => {
  assert.equal(pickSecondScenario('cat5', ['cat5']), null);
});

test('an unavailable `observed` is never chosen', () => {
  // The 1970 storms: seven bands, no observed.
  const bands = ['cat0', 'cat1', 'cat2', 'cat3', 'cat4', 'cat5', 'cat6'];
  assert.notEqual(pickSecondScenario('cat5', bands), 'observed');
});

