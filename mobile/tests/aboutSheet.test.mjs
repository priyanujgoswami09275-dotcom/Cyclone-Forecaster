/**
 * Pins the national-border disclosure in the About sheet.
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
 * **Where the sentence lives now.** Step 2 of the border-honesty work moved
 * it from literal copy in this component into the one shared constant in
 * `disclosures.ts`, so the native sheet and the web Impact tab render
 * byte-identical text and cannot drift apart. This file therefore pins two
 * things, which together are the same guarantee the original literal pin
 * gave: the constant's content, verbatim, and that the sheet renders that
 * constant — not some local string — inside the flood-limitation block,
 * next to the "about 0.5 km inland" claim, because the two are the same
 * kind of statement: limits of what the map's blue layer means. (The web
 * surface is pinned by `tests/disclosures.test.mjs`.)
 *
 * Whitespace is normalised before matching, because JSX text wraps across
 * lines and the wrap point is not part of the claim.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

import { BORDER_DISCLOSURE } from '../disclosures.ts';

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

  it('renders the shared constant, which carries the sentence verbatim', () => {
    assert.equal(BORDER_DISCLOSURE, BORDER_SENTENCE);
    assert.ok(norm.includes('{BORDER_DISCLOSURE}'), (
      'AboutSheet.tsx must render the shared BORDER_DISCLOSURE constant, ' +
      'not a component-local string — a local copy is where the native and ' +
      'web surfaces would drift apart.'
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
    const use = norm.indexOf('{BORDER_DISCLOSURE}');
    const next = norm.indexOf(NEXT_HEADING);
    assert.ok(heading >= 0, `missing the ${JSON.stringify(FLOOD_HEADING)} block`);
    assert.ok(next > heading, `missing the ${JSON.stringify(NEXT_HEADING)} block`);
    assert.ok(
      use > heading && use < next,
      'the border sentence belongs next to the flood-model limitation ' +
      'text, not in another block of the sheet',
    );
  });
});
