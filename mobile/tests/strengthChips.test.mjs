/**
 * Checks on the strength chips and the advisory's disabled state:
 *
 *   cd mobile && node --test 'tests/*.test.mjs'
 *
 * Two things are guarded, and both were specified rather than discovered:
 *
 *   1. **The chip-to-category mapping.** Four chips point at one preset and
 *      three category indices. Getting an index wrong is silent — the app
 *      renders, fetches a real category, and shows entirely real numbers for
 *      the wrong storm. Nothing throws, so only a pinned mapping catches it.
 *   2. **The disabled-state rule.** "Generate advisory" is disabled when there
 *      is no modelled exposure, and separately while a request is in flight.
 *      The second is the interesting one: pressing mid-flight would generate an
 *      advisory describing the *previous* chip's numbers.
 *
 * The category and overlay fixtures below are **taken from the live API**, not
 * invented — the real `/categories` and `/overlays` responses as of
 * 2026-09-30, trimmed to the fields these functions read. The real values are
 * what make "cat6 is 222 km/h and not a band midpoint" a fact rather than an
 * assumption, and the ≥ prefix in `figuresLine` depends on exactly that.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_CHIP,
  STRENGTH_CHIPS,
  advisoryEnabled,
  figuresLine,
  resolveChip,
} from '../strengthChips.ts';

/** The real `/categories` categories, as served. Trimmed to what is read. */
const CATEGORIES = [
  { category: 0, imd_category: 'Depression', wind_kmph: 40, wind_is_band_midpoint: true },
  { category: 1, imd_category: 'Deep Depression', wind_kmph: 55.5, wind_is_band_midpoint: true },
  { category: 2, imd_category: 'Cyclonic Storm', wind_kmph: 75, wind_is_band_midpoint: true },
  { category: 3, imd_category: 'Severe Cyclonic Storm', wind_kmph: 103, wind_is_band_midpoint: true },
  { category: 4, imd_category: 'Very Severe Cyclonic Storm', wind_kmph: 142, wind_is_band_midpoint: true },
  { category: 5, imd_category: 'Extremely Severe Cyclonic Storm', wind_kmph: 194, wind_is_band_midpoint: true },
  { category: 6, imd_category: 'Super Cyclonic Storm', wind_kmph: 222, wind_is_band_midpoint: false },
];

/** The real `/overlays` entries for the four chips, trimmed. */
const overlay = (id, wind_kmph, surge_m, final_land_area_km2) => ({
  id,
  wind_kmph,
  surge_m,
  final_land_area_km2,
});
const OVERLAYS = [
  overlay('cat4', 142, 1.829625708884688, 359.23),
  overlay('cat5', 194, 3.4149867674858227, 1709.57),
  overlay('cat6', 222, 4.471894139886578, 2680.22),
  overlay('remal_observed', 115, 1.2, 327.64),
];

const resolve = (id) => resolveChip(id, CATEGORIES, OVERLAYS);

// --- the mapping ---------------------------------------------------------

test('the four chips are the preset plus categories 4, 5 and 6', () => {
  // The brief's mapping, pinned. These are the four positions where something
  // is actually exposed: 0-3 flood nothing at all, and category 4 floods 359
  // km² without reaching an asset.
  assert.deepEqual(
    STRENGTH_CHIPS.map((c) => c.id),
    ['remal_observed', 'cat4', 'cat5', 'cat6'],
  );
  assert.deepEqual(
    STRENGTH_CHIPS.map((c) => c.label),
    ['Remal', 'Very severe', 'Extreme', 'Super cyclonic'],
  );
});

test('each band chip resolves to its own category index', () => {
  // The silent-failure case: a wrong index yields real numbers for the wrong
  // storm. Each chip is checked against its own id, not just "an index".
  assert.equal(resolve('cat4').categoryIndex, 4);
  assert.equal(resolve('cat5').categoryIndex, 5);
  assert.equal(resolve('cat6').categoryIndex, 6);
});

test('each band chip resolves to the category with that name', () => {
  // Guards the same failure from the other end: cat5 must not borrow cat4's
  // name, which would put "Very Severe" figures under an "Extreme" chip.
  assert.equal(resolve('cat4').borrowedCategory, 'Very Severe Cyclonic Storm');
  assert.equal(resolve('cat5').borrowedCategory, 'Extremely Severe Cyclonic Storm');
  assert.equal(resolve('cat6').borrowedCategory, 'Super Cyclonic Storm');
});

test('each band chip resolves to its own overlay', () => {
  for (const id of ['cat4', 'cat5', 'cat6', 'remal_observed']) {
    assert.equal(resolve(id).overlay?.id, id, `${id} picked up the wrong overlay`);
  }
});

test('the preset borrows no category and is flagged as borrowed', () => {
  // Remal made landfall at 115 kmph, which is Severe Cyclonic Storm and equal
  // to no band's midpoint. The preset is not a band, so it leaves the category
  // to the caller's `nearestCategory` and says so. `borrowed: true` is what
  // makes the panel disclose that its counts are the nearest band's.
  const preset = resolve('remal_observed');
  assert.equal(preset.categoryIndex, null);
  assert.equal(preset.borrowedCategory, null);
  assert.equal(preset.borrowed, true);
});

test('band chips are not flagged as borrowed', () => {
  for (const id of ['cat4', 'cat5', 'cat6']) {
    assert.equal(resolve(id).borrowed, false, `${id} should not claim a borrowed band`);
  }
});

test('the default chip is Super cyclonic', () => {
  assert.equal(DEFAULT_CHIP, 'cat6');
  assert.equal(resolve(DEFAULT_CHIP).categoryIndex, 6);
});

test('an empty category list degrades instead of throwing', () => {
  // `/categories` can fail. A chip press with nothing loaded must render a
  // loading state, not a crash on `categories[6]`.
  const r = resolveChip('cat6', [], OVERLAYS);
  assert.equal(r.categoryIndex, null);
  assert.equal(r.borrowedCategory, null);
  // The overlay is still there — the two indices are fetched separately.
  assert.equal(r.overlay?.id, 'cat6');
});

test('a missing overlay resolves to null rather than another chip\'s', () => {
  // The failure this catches is the nasty one: falling back to `overlays[0]`
  // would show Remal's raster under a chip labelled "Extreme".
  const r = resolveChip('cat5', CATEGORIES, [OVERLAYS[0]]);
  assert.equal(r.overlay, null);
  assert.equal(r.categoryIndex, 5);
});

test('an unknown chip id falls back to the first chip', () => {
  // Defensive: a stale persisted selection must not resolve to nothing.
  const r = resolveChip('cat99', CATEGORIES, OVERLAYS);
  assert.equal(r.chip.id, 'remal_observed');
});

// --- the figures line ----------------------------------------------------

test('an open-ended band is prefixed with ≥ and a midpoint is not', () => {
  // The live payload's own flag decides. Category 6 is 222 kmph with no upper
  // bound, so 222 is the floor of the band and "≥" is the only honest prefix.
  assert.equal(
    figuresLine(resolve('cat6').overlay, CATEGORIES[6].wind_is_band_midpoint),
    '≥222 km/h wind, 4.5 m surge, 2,680 km² flooded (model)',
  );
  // Category 4 is a real midpoint and needs no prefix.
  assert.equal(
    figuresLine(resolve('cat4').overlay, CATEGORIES[4].wind_is_band_midpoint),
    '142 km/h wind, 1.8 m surge, 359 km² flooded (model)',
  );
});

test('a missing midpoint flag is treated as not-a-midpoint, not as one', () => {
  // `undefined` means the field is absent, which means the value is not known
  // to be a midpoint. Defaulting to the bare number would assert something the
  // payload did not say, so the safe prefix wins.
  assert.ok(figuresLine(resolve('cat5').overlay, undefined).startsWith('≥194'));
});

test('no overlay yields an empty figures line, never "undefined"', () => {
  assert.equal(figuresLine(null, true), '');
  assert.equal(figuresLine(undefined, false), '');
});

// --- the disabled rule ---------------------------------------------------

test('exposure disables the advisory button', () => {
  assert.equal(advisoryEnabled(0, false), false);
});

test('any exposure enables it', () => {
  // One submerged asset is enough. A hospital is an evacuation problem even
  // if nothing else is touched.
  assert.equal(advisoryEnabled(1, false), true);
  assert.equal(advisoryEnabled(34, false), true);
});

test('a request in flight disables it regardless of the last count', () => {
  // The important one. Without this, switching from a populated chip to an
  // empty one and pressing immediately would generate an advisory describing
  // the *previous* chip's numbers.
  assert.equal(advisoryEnabled(34, true), false);
  assert.equal(advisoryEnabled(0, true), false);
});
