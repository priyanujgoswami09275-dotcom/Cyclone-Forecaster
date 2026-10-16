/**
 * Pins the shared border-disclosure constant and the web surface that
 * renders it.
 *
 *   cd mobile && node --test tests/disclosures.test.mjs
 *
 * Step 2 of the border honesty work: the sentence used to live as literal
 * copy in `AboutSheet.tsx` (native-only). It now lives in ONE exported
 * constant in `disclosures.ts` — a pure module with no react-native
 * imports — so the native sheet and the web Impact tab render
 * byte-identical text from one place and cannot drift apart.
 *
 * What this file pins:
 *
 *   1. The constant itself, verbatim — the exact approved sentence.
 *   2. `webViewModel.ts` exposes it through a pure helper, so the web tab
 *      renders a view-model value like every other figure label, not a
 *      component-local string.
 *   3. The web Impact tab renders that helper **inside the impact body,
 *      next to the flooded-area figure** — a disclosure the judges see on
 *      the main tab, not one hidden behind an About view.
 *
 * Checked as text for the placement pins, in the spirit of
 * `screenScenarioComposition.test.mjs`: this repository has no
 * component-rendering harness. Whitespace is normalised before matching,
 * because JSX text and attributes wrap across lines and the wrap point is
 * not part of any claim. (The native sheet's own pins, including its
 * rendering of this same constant, live in `aboutSheet.test.mjs`.)
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

import { BORDER_DISCLOSURE } from '../disclosures.ts';
import { floodedAreaNote } from '../webViewModel.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOBILE = resolve(HERE, '..');

/** The disclosure, verbatim — one sentence, no numbers, no jargon. */
const SENTENCE =
  'The flood extent is not clipped at the national border, so part of the ' +
  'flooded area and some listed facilities east of about 89 degrees E lie in ' +
  'Bangladesh.';

/** JSX text wraps across lines; the wrap point is not part of any claim. */
const normalise = (text) => text.replace(/\s+/g, ' ');

describe('the shared border-disclosure constant', () => {
  it('carries the approved sentence verbatim', () => {
    assert.equal(BORDER_DISCLOSURE, SENTENCE);
  });

  it('is what the web view-model exposes, byte-identical', () => {
    assert.equal(floodedAreaNote(), BORDER_DISCLOSURE);
  });
});

describe('the web Impact tab shows it next to the flooded-area figure', () => {
  const src = normalise(readFileSync(resolve(MOBILE, 'components', 'MapScreen.web.tsx'), 'utf8'));

  it('renders the view-model note inside the impact body', () => {
    const floodedFact = src.indexOf('<Fact label="Flooded land"');
    const note = src.indexOf('{floodedAreaNote()}');
    const tiles = src.indexOf('<View style={styles.tiles}>');
    assert.ok(floodedFact > 0, 'the "Flooded land" fact is missing from the source');
    assert.ok(note > 0, 'the Impact tab must render floodedAreaNote(), not a local string');
    assert.ok(tiles > note, 'anchor failed: the tiles block must come after the note');
    assert.ok(
      note > floodedFact && note < tiles,
      'the note belongs in the same block as the flooded-area figure — ' +
      'between the fact grid that shows it and the asset tiles',
    );
  });
});
