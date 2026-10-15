/**
 * Cyclone state: the labels a judge reads, and the arithmetic under a
 * comparison.
 *
 *   cd mobile && node --test 'tests/cycloneModel.test.mjs'
 *
 * These are string and number functions with no I/O, and that is exactly why
 * they are tested directly rather than through a rendered screen. Each one
 * guards a distinction that a UI can silently collapse:
 *
 *   1. `liveStateLabel` — three states, and the unhappy two must not read as
 *      each other. A feed that is *down* and a feed that is *empty* tell a
 *      reader opposite things, and "No active cyclone" is the sentence you
 *      must not print when nobody answered.
 *   2. `freshnessNote` — a failure reported without its time is a failure
 *      reported as current.
 *   3. `mlEstimateLabel` — the ML layer's gate failed, so its number is a
 *      median. Calling that a prediction is the single mislabel this whole
 *      branch exists to prevent.
 *   4. `comparisonDeltas` — a delta names its two ends, and rounds the way
 *      the backend rounds.
 *
 * Fixtures are taken from the real payloads: the `live_unavailable` reason is
 * the backend's own `live_unavailable_reason()` plus its per-endpoint
 * diagnostic, and the ML figures are the measured gate result.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  comparisonDeltas,
  cycloneDisplayName,
  freshnessNote,
  liveStateLabel,
  mlEstimateLabel,
} from '../cycloneModel.ts';

/** The backend's real composed reason: promise first, then the diagnostic. */
const UNAVAILABLE_REASON =
  'No live cyclone feed could be reached, so nothing is being shown in its ' +
  'place and no historical cyclone is substituted for the live feed. ' +
  'Attempt made at 2026-10-01T00:00:00Z. Sources tried: 403, 403, 404, 200.';

const UNAVAILABLE = {
  status: 'live_unavailable',
  reason: UNAVAILABLE_REASON,
  source: 'nrlmry.navy.mil',
  http_status: 403,
  checked_at: '2026-10-01T00:00:00Z',
  cyclone: null,
};

test('an unavailable live feed never says a storm is happening', () => {
  const label = liveStateLabel(UNAVAILABLE);
  assert.match(label, /unavailable/i);
  // The three ways this could go wrong, each of which asserts a storm exists.
  assert.doesNotMatch(label, /live storm|active cyclone|watch/i);
});

test('no_active_storm is distinct from unavailable', () => {
  assert.notStrictEqual(
    liveStateLabel({ status: 'no_active_storm' }),
    liveStateLabel({ status: 'live_unavailable' }),
  );
  // A working feed with nothing in it is not a broken feed.
  assert.match(liveStateLabel({ status: 'no_active_storm' }), /no active/i);
  assert.match(liveStateLabel({ status: 'live_unavailable' }), /unavailable/i);
});

test('a live feed that answered is labelled Live, and the name is separate', () => {
  // The label is one of three closed strings — the storm's name is the caller's
  // to append, because a name is a claim and only `available` licenses one.
  const state = {
    status: 'available',
    cyclone: { name: 'DANA', latest_wind_kmph: 111 },
  };
  assert.equal(liveStateLabel(state), 'Live');
  assert.equal(state.cyclone?.name, 'DANA');
  assert.doesNotMatch(liveStateLabel(state), /unavailable|no active/i);
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

// ---------------------------------------------------------------------------
// The masthead title.
//
// The H1 used to be the literal string "Cyclone Remal, May 2024" while the map
// could be showing a 1970 storm's track — a case-study name substituted over
// whatever was actually selected, which is the mislabel this whole system
// exists to avoid. The title now follows the selection, so it needs the
// catalogue's uppercase IBTrACS name rendered for a human.
// ---------------------------------------------------------------------------

test('a cyclone name is title-cased for display', () => {
  assert.equal(cycloneDisplayName('REMAL'), 'Remal');
  assert.equal(cycloneDisplayName('UNNAMED'), 'Unnamed');
  assert.equal(cycloneDisplayName('DANA'), 'Dana');
});

test('separator characters in a name are each title-cased', () => {
  // IBTrACS carries paired names for storms named differently by two agencies.
  assert.equal(cycloneDisplayName('BESS:BONNIE'), 'Bess:Bonnie');
  assert.equal(cycloneDisplayName('KHAI-MUK'), 'Khai-Muk');
  assert.equal(cycloneDisplayName('MAHASEN:VIYARU'), 'Mahasen:Viyaru');
});

test('an empty or missing name renders as an absence, not "undefined"', () => {
  assert.equal(cycloneDisplayName(''), 'Unnamed');
  assert.equal(cycloneDisplayName(null), 'Unnamed');
});

test('an ml estimate that actually beat the gate may say so', () => {
  assert.match(mlEstimateLabel({ beats_baseline: true }), /estimate/i);
});

test('deltas name the two scenarios being compared — plain names when the payload provides them', () => {
  const rows = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137,
      imd_category: 'Extremely Severe Cyclonic Storm', scenario_kind: 'band_midpoint' },
    { cyclone_id: 'A', scenario_id: 'observed', surge_m: 1.12, exposed: 0,
      imd_category: 'Severe Cyclonic Storm', scenario_kind: 'observed' },
  ]);
  assert.equal(rows[0].label, 'From Extremely Severe Cyclonic Storm to the observed storm');
  assert.equal(rows[0].surge_delta_m, -2.29);
  assert.equal(rows[0].exposed_delta, -137);
});

test('deltas work in both orders — the sign follows the direction', () => {
  // Order 1: band first, observed second → negative deltas (going down)
  const down = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137,
      imd_category: 'Extremely Severe Cyclonic Storm', scenario_kind: 'band_midpoint' },
    { cyclone_id: 'A', scenario_id: 'observed', surge_m: 1.12, exposed: 0,
      imd_category: 'Severe Cyclonic Storm', scenario_kind: 'observed' },
  ]);
  assert.equal(down[0].label, 'From Extremely Severe Cyclonic Storm to the observed storm');
  assert.equal(down[0].surge_delta_m, -2.29);
  assert.equal(down[0].exposed_delta, -137);

  // Order 2: observed first, band second → positive deltas (going up)
  const up = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'observed', surge_m: 1.12, exposed: 0,
      imd_category: 'Severe Cyclonic Storm', scenario_kind: 'observed' },
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137,
      imd_category: 'Extremely Severe Cyclonic Storm', scenario_kind: 'band_midpoint' },
  ]);
  assert.equal(up[0].label, 'From the observed storm to Extremely Severe Cyclonic Storm');
  assert.equal(up[0].surge_delta_m, 2.29);
  assert.equal(up[0].exposed_delta, 137);
});

test('deltas use storm names when the scenario is the same across storms', () => {
  const rows = comparisonDeltas([
    { cyclone_id: '2024145N14087', scenario_id: 'cat6', surge_m: 4.47, exposed: 285,
      name: 'REMAL', imd_category: 'Super Cyclonic Storm', scenario_kind: 'category' },
    { cyclone_id: '2023301N12000', scenario_id: 'cat6', surge_m: 4.47, exposed: 285,
      name: 'BIPARJOY', imd_category: 'Super Cyclonic Storm', scenario_kind: 'category' },
  ]);
  assert.equal(rows[0].label, 'From REMAL to BIPARJOY');
  // At the same scenario the figures are identical — a zero delta is a
  // fact, not a gap (the same rule as `exposureText`).
  assert.equal(rows[0].surge_delta_m, 0);
  assert.equal(rows[0].exposed_delta, 0);
});

test('deltas fall back to scenario ids when the payload provides no class name', () => {
  // Native callers that haven't been updated to pass imd_category still
  // get a working label — the raw id, same as before the plain-name change.
  const rows = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'cat4', surge_m: 1.83, exposed: 0 },
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137 },
  ]);
  assert.equal(rows[0].label, 'From cat4 to cat5');
  assert.equal(rows[0].surge_delta_m, 1.58);
});

test('a delta between one row and nothing is not a delta', () => {
  assert.deepEqual(
    comparisonDeltas([{ cyclone_id: 'A', scenario_id: 'cat4', surge_m: 1.83, exposed: 0 }]),
    [],
    'one row has nothing to be compared against, so there is no delta to show',
  );
});

// ---------------------------------------------------------------------------
// Per-type deltas.
//
// The old line summed hospitals + substations + road segments into one number
// and printed "N hospitals, substations and road segments reached", which a
// reader parses as N of each. The counts are now carried separately, with the
// same sign convention as the surge (right minus left, matching the arrow).
// ---------------------------------------------------------------------------

test('per-type deltas name each exposure type — cat5 vs observed gives -5, -10, -122', () => {
  // The real figures: cat5 exposes 5 hospitals / 10 substations / 122 road
  // segments; the observed storm exposes none.
  const rows = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137,
      hospitals: 5, substations: 10, roads: 122,
      imd_category: 'Extremely Severe Cyclonic Storm', scenario_kind: 'band_midpoint' },
    { cyclone_id: 'A', scenario_id: 'observed', surge_m: 1.12, exposed: 0,
      hospitals: 0, substations: 0, roads: 0,
      imd_category: 'Severe Cyclonic Storm', scenario_kind: 'observed' },
  ]);
  assert.equal(rows[0].hospitals_delta, -5);
  assert.equal(rows[0].substations_delta, -10);
  assert.equal(rows[0].roads_delta, -122);
  // The summed contract still holds alongside the per-type figures.
  assert.equal(rows[0].exposed_delta, -137);
  assert.equal(rows[0].surge_delta_m, -2.29);
});

test('per-type deltas follow the direction — both orders', () => {
  const cat5 = { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137,
    hospitals: 5, substations: 10, roads: 122,
    imd_category: 'Extremely Severe Cyclonic Storm', scenario_kind: 'band_midpoint' };
  const observed = { cyclone_id: 'A', scenario_id: 'observed', surge_m: 1.12, exposed: 0,
    hospitals: 0, substations: 0, roads: 0,
    imd_category: 'Severe Cyclonic Storm', scenario_kind: 'observed' };
  const down = comparisonDeltas([cat5, observed]);
  assert.equal(down[0].hospitals_delta, -5);
  assert.equal(down[0].substations_delta, -10);
  assert.equal(down[0].roads_delta, -122);
  const up = comparisonDeltas([observed, cat5]);
  assert.equal(up[0].hospitals_delta, 5);
  assert.equal(up[0].substations_delta, 10);
  assert.equal(up[0].roads_delta, 122);
});

test('rows with only the summed field keep it working — per-type stays unknown, not zero', () => {
  // A regression guard on the old contract: callers that pass only `exposed`
  // still get `exposed_delta`, and the per-type deltas are ABSENT — an
  // unknown count must not read as "no change".
  const rows = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'cat4', surge_m: 1.83, exposed: 0 },
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137 },
  ]);
  assert.equal(rows[0].exposed_delta, 137);
  assert.equal(rows[0].hospitals_delta, undefined);
  assert.equal(rows[0].substations_delta, undefined);
  assert.equal(rows[0].roads_delta, undefined);
});
