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

test('an ml estimate that actually beat the gate may say so', () => {
  assert.match(mlEstimateLabel({ beats_baseline: true }), /estimate/i);
});

test('deltas name the two scenarios being compared', () => {
  const rows = comparisonDeltas([
    { cyclone_id: 'A', scenario_id: 'cat4', surge_m: 1.83, exposed: 0 },
    { cyclone_id: 'A', scenario_id: 'cat5', surge_m: 3.41, exposed: 137 },
  ]);
  assert.equal(rows[0].label, 'cat4 → cat5');
  assert.equal(rows[0].surge_delta_m, 1.58);
});

test('a delta between one row and nothing is not a delta', () => {
  assert.deepEqual(
    comparisonDeltas([{ cyclone_id: 'A', scenario_id: 'cat4', surge_m: 1.83, exposed: 0 }]),
    [],
    'one row has nothing to be compared against, so there is no delta to show',
  );
});
