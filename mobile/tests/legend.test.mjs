/**
 * Checks on the map legend's data, run with the stdlib test runner:
 *
 *   cd mobile && node --test 'tests/*.test.mjs'
 *
 * What is being guarded is the *agreement* between the legend and the map: that
 * each row names the constant its layer is actually drawn in. This is the one
 * property of a legend that can be true by accident and then quietly stop
 * being true, because `theme` is a mutable object and retuning a token has to
 * move the map and its key together.
 *
 * The data lives in `mobile/legend.ts` rather than inline in `MapLegend.tsx`
 * for two reasons, and both are load-bearing:
 *
 *   1. So this file can reach it. `legend.ts` imports nothing, because
 *      `theme.ts` and `mapStyles.ts` are imported extensionless (as Metro
 *      requires) and Node's ESM resolver will not follow that.
 *   2. So the check is not a copy. An earlier draft had the colours inline and
 *      the only way to test them would have been to retype them here — a test
 *      that agrees with whatever it is copied from, which is exactly the shape
 *      of the invalid-PNG bug where `decode_png_rgba` checked `encode_png_rgba`
 *      and both were wrong. See MEMORY.md flag 42. Instead each row *names* its
 *      source and the component resolves it, so there is still one expression
 *      of each colour in the app.
 *
 * Plain `.mjs` for the reason `track.test.mjs` gives — type-checking these
 * would mean changing the `tsconfig.json` the app itself builds with.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LEGEND_ROWS } from '../legend.ts';

const byId = (id) => LEGEND_ROWS.find((row) => row.id === id);

test('there are exactly five rows, and they are the five layers', () => {
  // The brief names five. A layer added to the map without a legend row is the
  // failure this catches; a row for a layer that is not drawn is the other one,
  // which the per-row checks below cannot see.
  assert.equal(LEGEND_ROWS.length, 5);
  assert.deepEqual(
    LEGEND_ROWS.map((r) => r.id),
    ['flood', 'hospital', 'substation', 'road', 'stormpath'],
  );
});

test('every row has a non-empty label', () => {
  for (const row of LEGEND_ROWS) {
    assert.ok(row.label.length > 0, `${row.id} has no label`);
  }
});

test('the labels name the five things a reader can see', () => {
  // Pinned as a set rather than an order: the wording is the layer's meaning,
  // and a reword that turns "Cut-off road" into "Flooded road" would ship a
  // claim the layer does not make — it is the *road network* that is severed,
  // not the road surface.
  assert.deepEqual(
    LEGEND_ROWS.map((r) => r.label).sort(),
    ['Cut-off road', 'Flooded area', 'Hospital', 'Storm path', 'Substation'],
  );
});

test('each point asset names its own pin colour, not the other one', () => {
  // The specific failure here is both assets pointing at `hospital`: the two
  // pins are `danger` and `caution`, and a legend that showed the same swatch
  // twice would leave the reader unable to tell a hospital from a substation —
  // which is the whole reason those two pins differ in colour.
  assert.equal(byId('hospital').fillSource, 'mapStyles.assetPinColours.hospital');
  assert.equal(byId('substation').fillSource, 'mapStyles.assetPinColours.substation');
  assert.notEqual(
    byId('hospital').fillSource,
    byId('substation').fillSource,
    'hospital and substation must not share a swatch source',
  );
});

test('the cut-off road and storm path name their own line colours', () => {
  // Roads are `danger`, the track is `text`. If these two were swapped the
  // legend would still be internally consistent and still be wrong, which is
  // why each is pinned to its own layer rather than to "not the other one".
  assert.equal(byId('road').fillSource, 'mapStyles.compromisedRoadStyle.strokeColor');
  assert.equal(byId('stormpath').fillSource, 'mapStyles.trackLineStyle.strokeColor');
});

test('the flood row names the fill and the stroke, and no other row names a stroke', () => {
  // The flood layer is the only one drawn as a fill plus a stroke, so it is the
  // only row with a `strokeSource`. A second one would mean a shape in the
  // legend that the map does not draw.
  assert.equal(byId('flood').fillSource, 'theme.flood');
  assert.equal(byId('flood').strokeSource, 'theme.water');
  for (const row of LEGEND_ROWS.filter((r) => r.id !== 'flood')) {
    assert.equal(row.strokeSource, undefined, `${row.id} should not name a stroke`);
  }
});

test('every fill source is a real export path, and every row has one', () => {
  // These strings are resolved by a `switch` in MapLegend.tsx, which falls
  // through to `null` for anything unrecognised. An unrecognised source
  // renders as a muted grey swatch — visible, but a legend that lies quietly.
  // So the set of names is pinned closed here, and the type keeps the two in
  // step: adding a name to the union without adding a `case` fails the type
  // check, adding a `case` without a row fails this.
  const KNOWN = new Set([
    'theme.flood',
    'theme.water',
    'mapStyles.assetPinColours.hospital',
    'mapStyles.assetPinColours.substation',
    'mapStyles.compromisedRoadStyle.strokeColor',
    'mapStyles.trackLineStyle.strokeColor',
  ]);
  for (const row of LEGEND_ROWS) {
    assert.ok(row.fillSource, `${row.id} names no fill source`);
    assert.ok(KNOWN.has(row.fillSource), `${row.id} names unknown source ${row.fillSource}`);
    if (row.strokeSource) {
      assert.ok(KNOWN.has(row.strokeSource), `${row.id} names unknown stroke ${row.strokeSource}`);
    }
  }
});

test('the point assets are pins and the linear layers are lines', () => {
  // The shape is not decoration: a pin drawn as a bar would tell a reader the
  // hospitals are lines, and the dash is the only thing marking a road as
  // cut-off rather than merely coloured.
  assert.equal(byId('hospital').kind, 'pin');
  assert.equal(byId('substation').kind, 'pin');
  assert.equal(byId('road').kind, 'dash');
  assert.equal(byId('stormpath').kind, 'path');
  assert.equal(byId('flood').kind, 'flood');
});

test('no two rows name the same fill source', () => {
  // Two rows resolving to one colour would show the same swatch twice under
  // two names, and the reader has no way to tell them apart. The flood row is
  // included, so this also catches a linear layer accidentally pointed at
  // `flood`.
  const sources = LEGEND_ROWS.map((r) => r.fillSource);
  assert.equal(new Set(sources).size, sources.length, `duplicate swatch sources: ${sources.join(', ')}`);
});
