/**
 * Pins the national-border disclosure sentence in the About sheet.
 *
 *   cd mobile && node --test tests/aboutSheet.test.mjs
 *
 * **Why a source pin.** The sentence is app copy with no payload behind it —
 * nothing at request time computes it, so nothing but a test keeps it from
 * being "tidied" away. This repository has no component-rendering harness,
 * so it is checked as text, in the spirit of `webBundleSafety.test.mjs` and
 * `screenScenarioComposition.test.mjs`.
 *
 * **What the sentence is.** The flood engine runs over the DEM bbox, which
 * reaches 89.2°E — into Bangladesh — and nothing clips the extent at the
 * border. Measured against a provisional lon 89.0 proxy line (the repo has
 * no boundary dataset; see CLAUDE.md's gotchas for the numbers and the
 * convention), roughly a fifth of the flooded land at every scenario lies
 * east of it, and a handful of counted roads plus one hospital do too.
 *
 * Placement is pinned as well as presence: the sentence belongs next to the
 * existing flood-model limitation ("about 0.5 km inland"), because the two
 * are the same kind of claim — limits of what the map's blue layer means.
 * Whitespace is normalised before matching, because JSX text wraps across
 * lines and the wrap point is not part of the claim.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = resolve(HERE, '..', 'components', 'AboutSheet.tsx');

/** The disclosure, verbatim — the exact sentence the About sheet must carry. */
const BORDER_SENTENCE =
  'The flood extent is not clipped at the national border, so part of the ' +
  'flooded area and some listed facilities east of about 89 degrees E lie in ' +
  'Bangladesh.';

/** The block the sentence lives in, and the one after it. */
const FLOOD_HEADING = 'How wide the flood can spread';
const NEXT_HEADING = 'The storm path';

/** JSX text wraps across lines; the wrap point is not part of any claim. */
const normalise = (text) => text.replace(/\s+/g, ' ');

describe('AboutSheet border disclosure', () => {
  const norm = normalise(readFileSync(SOURCE, 'utf8'));

  it('carries the border sentence verbatim', () => {
    assert.ok(norm.includes(BORDER_SENTENCE), (
      'AboutSheet.tsx must carry the national-border disclosure sentence ' +
      'verbatim. It is the one place a reader learns the flood extent is ' +
      'not clipped at the border.'
    ));
  });

  it('keeps the existing flood-limitation claim beside it', () => {
    assert.ok(norm.includes('about 0.5 km inland'), (
      'the existing flood-model limitation (about 0.5 km inland) must ' +
      'survive any edit to the block the border sentence was added to'
    ));
  });

  it('places the sentence inside the flood-limitation block', () => {
    const heading = norm.indexOf(FLOOD_HEADING);
    const sentence = norm.indexOf(BORDER_SENTENCE);
    const next = norm.indexOf(NEXT_HEADING);
    assert.ok(heading >= 0, `missing the ${JSON.stringify(FLOOD_HEADING)} block`);
    assert.ok(next > heading, `missing the ${JSON.stringify(NEXT_HEADING)} block`);
    assert.ok(
      sentence > heading && sentence < next,
      'the border sentence belongs next to the flood-model limitation ' +
      'text, not in another block of the sheet',
    );
  });
});
