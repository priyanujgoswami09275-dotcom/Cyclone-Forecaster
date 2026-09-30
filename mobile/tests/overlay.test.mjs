/**
 * One check for whether a flood layer is drawn at all, run with the stdlib
 * test runner:
 *
 *   cd mobile && node --test 'tests/*.test.mjs'
 *
 * `api.ts` imports nothing from react-native, which is what makes this
 * possible without a Jest/React-Native rig — the property the file's own
 * header claims, and this is the thing that verifies it. See
 * `track.test.mjs` for why this is plain `.mjs` and not type-checked.
 *
 * What is being guarded: the condition that decides whether `<Overlay>` is
 * rendered. It used to be `overlay ? overlayImageUrl(overlay) ?? '' : ''`,
 * which always rendered an `<Overlay>` — handing the map an empty `uri` and a
 * set of bounds for categories 0-3, whose overlays are real images with no
 * water in them at all.
 *
 * The pixel counts below are the real committed ones from
 * `data/overlays/overlays.json`, not invented: 0, 0, 0, 0, 16052, 76327,
 * 119406, 14657. Four of the eight entries are zero, so "does this draw"
 * and "does this exist" are genuinely different questions.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { shouldDrawOverlay } from '../api.ts';

/** A committed overlay entry, with only the field the decision reads. */
function entry(overrides = {}) {
  return { id: 'cat4', image_url: '/overlays/flood_cat4.png', flooded_pixels: 16052, ...overrides };
}

test('an overlay with water is drawn', () => {
  assert.equal(shouldDrawOverlay(entry()), true);
});

test('an overlay with no water is not drawn', () => {
  // The actual bug. cat0-cat3 report 0 flooded pixels, which is correct --
  // the flood model puts no water on land at those intensities. Rendering a
  // layer for them asks the map to place a bounds tuple over a texture that
  // does not exist.
  assert.equal(shouldDrawOverlay(entry({ id: 'cat0', flooded_pixels: 0 })), false);
});

test('no entry at all is not drawn', () => {
  // The index is still loading, or this category has no entry.
  assert.equal(shouldDrawOverlay(null), false);
  assert.equal(shouldDrawOverlay(undefined), false);
});

test('a missing or malformed count fails closed', () => {
  // `undefined > 0` is false, so an index that loses the field stops being
  // drawn. The other failure direction would draw a solid rectangle over the
  // delta, because the client cannot tell an absent count from a large one.
  assert.equal(shouldDrawOverlay(entry({ flooded_pixels: undefined })), false);
  assert.equal(shouldDrawOverlay({ id: 'cat4' }), false);
  assert.equal(shouldDrawOverlay(entry({ flooded_pixels: null })), false);
  assert.equal(shouldDrawOverlay(entry({ flooded_pixels: '16052' })), false);
  assert.equal(shouldDrawOverlay(entry({ flooded_pixels: Number.NaN })), false);
});

test('one pixel is enough to draw', () => {
  // The threshold is > 0, not "a visible amount". A single flooded pixel is
  // still a modelled inundation, and silently dropping it would be the same
  // class of error as dropping a whole inlet.
  assert.equal(shouldDrawOverlay(entry({ flooded_pixels: 1 })), true);
});

test('the eight committed overlays split four and four', () => {
  // Pinned against the real index so a change in the model's output cannot
  // quietly make every category "empty" and leave the app drawing nothing
  // while still reporting flooded areas everywhere else.
  const committed = {
    cat0: 0, cat1: 0, cat2: 0, cat3: 0,
    cat4: 16052, cat5: 76327, cat6: 119406, remal_observed: 14657,
  };
  const drawn = Object.entries(committed).filter(([, px]) => px > 0);
  const blank = Object.entries(committed).filter(([, px]) => px === 0);
  assert.equal(drawn.length, 4);
  assert.equal(blank.length, 4);
  for (const [id, px] of Object.entries(committed)) {
    assert.equal(
      shouldDrawOverlay(entry({ id, flooded_pixels: px })),
      px > 0,
      `${id} (${px} px) drew or did not draw wrongly`,
    );
  }
});
