/**
 * Checks on the exposure tiles' counts and their empty state:
 *
 *   cd mobile && node --test 'tests/*.test.mjs'
 *
 * The tiles are what a judge reads first, and the failure mode is not a crash —
 * it is a plausible number. Three things are guarded:
 *
 *   1. **Null is not zero.** "Not loaded yet" and "loaded, and there is
 *      nothing there" are different claims, and the tiles render the first as
 *      `—` and the second as `0`. A module that collapsed null to 0 would
 *      make the map say a category exposes nothing while it was still fetching.
 *   2. **The captions are not interchangeable.** A hospital is *submerged*; a
 *      road is *cut off by water* it need not be standing in. Same number,
 *      different claim.
 *   3. **The empty-state copy is the specified string**, word for word.
 *
 * The category-6 counts below are the real ones, measured against the live
 * `/exposure` response, not invented.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  EMPTY_EXPOSURE_LINE,
  buildTiles,
  totalExposed,
} from '../exposureTiles.ts';

/** The real /exposure counts at Super Cyclonic Storm (category 6). */
const CATEGORY_6 = { hospitals: 9, substations: 25, roads: 251 };

const tilesOf = (input) => buildTiles(input);
const byId = (input, id) => tilesOf(input).find((t) => t.id === id);

// --- the three tiles -----------------------------------------------------

test('there are three tiles, in the specified order', () => {
  assert.deepEqual(
    tilesOf(CATEGORY_6).map((t) => t.id),
    ['hospitals', 'substations', 'roads'],
  );
});

test('the labels are the specified ones', () => {
  // "Roads cut" and not "Roads cut off": the caption line below already says
  // "cut off by water", and the pair reads as a duplicate otherwise.
  assert.deepEqual(
    tilesOf(CATEGORY_6).map((t) => t.label),
    ['Hospitals', 'Substations', 'Roads cut'],
  );
});

test('the captions distinguish submerged assets from severed roads', () => {
  // A road is cut off by water it is not in. Rendering all three as
  // "submerged" would state something false about 251 polylines.
  assert.equal(byId(CATEGORY_6, 'hospitals').unit, 'submerged');
  assert.equal(byId(CATEGORY_6, 'substations').unit, 'submerged');
  assert.equal(byId(CATEGORY_6, 'roads').unit, 'cut off by water');
});

// --- null is not zero ----------------------------------------------------

test('a null count survives as null and is not turned into 0', () => {
  // The component renders null as an em dash. If this returned 0 the tile
  // would claim "0 hospitals" while the request was still in flight.
  const loading = { hospitals: null, substations: null, roads: null };
  assert.equal(byId(loading, 'hospitals').count, null);
  assert.equal(byId(loading, 'roads').count, null);
});

test('a partial load keeps its nulls and its numbers apart', () => {
  // Two tiles resolved and one still loading is a real state, and it must not
  // collapse: the reader needs to know *which* counts are missing.
  const partial = { hospitals: 9, substations: null, roads: 251 };
  assert.equal(byId(partial, 'hospitals').count, 9);
  assert.equal(byId(partial, 'substations').count, null);
  assert.equal(byId(partial, 'roads').count, 251);
});

test('a real zero stays a zero and is not confused with null', () => {
  const none = { hospitals: 0, substations: 0, roads: 0 };
  assert.equal(byId(none, 'hospitals').count, 0);
  assert.notEqual(byId(none, 'hospitals').count, null);
});

test('the total treats an unresolved count as zero', () => {
  // Deliberate, and the reason `advisoryEnabled` also takes the loading flag:
  // collapsing null to 0 here can only ever *disable* the button, never enable
  // it, so a null cannot produce a spurious "there is exposure".
  assert.equal(totalExposed(CATEGORY_6), 285);
  assert.equal(totalExposed({ hospitals: null, substations: null, roads: null }), 0);
  assert.equal(totalExposed({ hospitals: 0, substations: null, roads: 3 }), 3);
});

test('the total of the real category-6 figures is 285', () => {
  // Pinned arithmetic on real numbers. 9 + 25 + 251, and the number the
  // Generate button's enabled state turns on.
  assert.equal(totalExposed(CATEGORY_6), 9 + 25 + 251);
});

// --- the empty state -----------------------------------------------------

test('the empty-state line is the specified copy, word for word', () => {
  assert.equal(EMPTY_EXPOSURE_LINE, 'No modelled exposure at this strength');
});

test('both words in the empty state are load-bearing', () => {
  // "Modelled" — the app ran a model over a DEM, it observed nothing, and a
  // real storm at this strength might flood assets the model missed. "At this
  // strength" — the reader's next move is another chip, and the line has to
  // invite it rather than read as a dead end. Dropping either changes the
  // claim, so both are pinned.
  assert.match(EMPTY_EXPOSURE_LINE, /modelled/);
  assert.match(EMPTY_EXPOSURE_LINE, /at this strength/);
});

test('the empty state does not claim there was no water', () => {
  // The exact case that fires it: the preset's borrowed band floods 359 km²
  // and reaches no asset. A line saying "no flooding at this strength" would
  // be plainly contradicted by the blue layer right above it.
  assert.doesNotMatch(EMPTY_EXPOSURE_LINE, /no flood/i);
  assert.doesNotMatch(EMPTY_EXPOSURE_LINE, /no water/i);
});
