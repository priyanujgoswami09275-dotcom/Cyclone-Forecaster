/**
 * Checks on the track facts the About sheet states in prose:
 *
 *   cd mobile && node --test 'tests/*.test.mjs'
 *
 * This module exists because of one specific hazard in the committed source
 * data, and the hazard is invisible at the call site. `data/remal_track.geojson`
 * writes a blank IBTrACS `USA_WIND` as `0.0`, so "not reported" and "calm"
 * arrive looking identical. The backend separates them into `wind_kmph: null`
 * with `wind_reported: false`; these tests are what stops the app from undoing
 * that separation in the one place it would matter — the sentence it shows a
 * reader about the track's strongest wind.
 *
 * The fixtures below are the real 19 committed fixes, in order, as the endpoint
 * serves them: 14 reported and 5 unreported, peaking at 54 kt (100.0 km/h).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  KNOTS_TO_KMPH,
  countUnreported,
  peakReportedWindKmph,
  windNeedsUnitCaveat,
} from '../trackFacts.ts';

/**
 * 54 kt, the peak, in km/h **as the endpoint serves it**.
 *
 * `main.py` rounds the conversion to one decimal, so the payload carries
 * `100.0` and not `100.00800000000001`. The fixtures below use the rounded
 * values deliberately: this module's job is to summarise what the endpoint
 * hands it, and a test that asserted the unrounded product would be testing
 * arithmetic the app never performs.
 */
const PEAK_KMPH = 100.0;

/** The real 19 fixes, reduced to the two fields this module reads. */
const fix = (wind_kmph, wind_reported) => ({ wind_kmph, wind_reported });
const REAL_TRACK = [
  fix(64.8, true),  // 35 kt
  fix(64.8, true),
  fix(64.8, true),
  fix(68.5, true),  // 37 kt
  fix(72.2, true),  // 39 kt
  fix(83.3, true),  // 45 kt
  fix(94.4, true),  // 51 kt
  fix(96.3, true),  // 52 kt
  fix(100.0, true), // 54 kt — the peak
  fix(100.0, true),
  fix(100.0, true),
  fix(96.3, true),
  fix(94.4, true),
  fix(null, false), // blank USA_WIND
  fix(null, false),
  fix(null, false),
  fix(null, false),
  fix(null, false),
  fix(72.2, true),  // 39 kt — the final fix, back in the Bay
];

// --- the peak ------------------------------------------------------------

test('the peak of the real track is its strongest reported fix', () => {
  assert.equal(peakReportedWindKmph(REAL_TRACK), PEAK_KMPH);
});

test('an unreported fix is skipped, not read as calm', () => {
  // The failure this exists to catch. If the filter were `wind_kmph !== null`
  // alone, or if a 0 were treated as a value, the sheet would say the track
  // "peaks at 0 km/h" — which reads as a measurement of a dead storm and is
  // false. The five blanks sit either side of the real 39 kt final fix, so a
  // wrong peak is not a rounding difference but a different storm.
  const onlyBlanks = [fix(null, false), fix(null, false)];
  assert.equal(peakReportedWindKmph(onlyBlanks), null);
  assert.equal(peakReportedWindKmph(REAL_TRACK), PEAK_KMPH);
});

test('a genuinely reported zero IS a peak', () => {
  // The mirror of the test above, and the reason the filter is on
  // `wind_reported` rather than on the value being non-zero. A best-track
  // agency does report 0 kt for a dissipated system, and dropping it would
  // understate a track that weakens to nothing.
  assert.equal(peakReportedWindKmph([fix(0, true)]), 0);
});

test('a reported wind that is null is still skipped', () => {
  // `wind_reported: true` with a null wind is a malformed fix, not a
  // measurement. Taking it would make `null` the peak and poison every
  // comparison downstream.
  assert.equal(peakReportedWindKmph([fix(null, true), fix(80, true)]), 80);
  assert.equal(peakReportedWindKmph([fix(null, true)]), null);
});

test('a non-finite reported wind is skipped, not compared', () => {
  // `NaN > peak` is false for every peak, so a NaN would survive a naive
  // `>` comparison when it happened to be seen first, and then nothing would
  // ever beat it. The endpoint does not emit these, but the file is read from
  // disk and `json.loads` accepts a bare `NaN`.
  assert.equal(peakReportedWindKmph([fix(Number.NaN, true), fix(80, true)]), 80);
  assert.equal(peakReportedWindKmph([fix(Number.POSITIVE_INFINITY, true)]), null);
});

test('an empty track has no peak rather than a peak of zero', () => {
  assert.equal(peakReportedWindKmph([]), null);
});

// --- the unreported count -----------------------------------------------

test('the real track has five unreported fixes', () => {
  // Pinned, because the About sheet's sentence ("5 of these fixes report no
  // wind") is a claim about the committed file. If the file is re-fetched and
  // the blanks change, this fails and the copy needs re-reading — which is the
  // correct outcome, since a number a reader can check should not drift
  // silently.
  assert.equal(REAL_TRACK.length, 19);
  assert.equal(countUnreported(REAL_TRACK), 5);
});

test('a fully reported track counts zero', () => {
  assert.equal(countUnreported([fix(80, true), fix(90, true)]), 0);
});

// --- the unit caveat -----------------------------------------------------

test('a track peak below IMD’s figure is flagged as needing the unit caveat', () => {
  // The real case: 100 km/h against IMD's 110-120. Without the caveat a reader
  // concludes the app's own >=222 km/h chip is far more extreme than anything
  // observed, which is a comparison two different averaging periods do not
  // support.
  assert.equal(windNeedsUnitCaveat(PEAK_KMPH, 110), true);
  assert.equal(windNeedsUnitCaveat(PEAK_KMPH, 120), true);
});

test('a track peak at or above IMD’s figure needs no caveat', () => {
  // Not a silent pass: if the source data ever changes to a 1-minute figure
  // above IMD's, the sentence about differing averages would be a claim the
  // numbers no longer support.
  assert.equal(windNeedsUnitCaveat(130, 110), false);
  assert.equal(windNeedsUnitCaveat(110, 110), false);
});

test('no peak needs no caveat — there is nothing to compare', () => {
  assert.equal(windNeedsUnitCaveat(null, 110), false);
});

// --- the conversion constant --------------------------------------------

test('the knots constant is the real one, not 1.85', () => {
  // 1.85 is the number people write from memory and it is wrong by 0.1%. Over
  // a 54 kt peak that is 1 km/h, and the figure is quoted to the reader as a
  // measurement of a real cyclone. The endpoint applies the same constant, so
  // the two agree by construction rather than by coincidence.
  assert.equal(KNOTS_TO_KMPH, 1.852);
  assert.equal(Math.round(54 * KNOTS_TO_KMPH), 100);
  // And it is the endpoint's own rounding, not this test's:
  assert.equal(Math.round(54 * KNOTS_TO_KMPH * 10) / 10, PEAK_KMPH);
});
