/**
 * The screens must never hand a chip id to a request builder as a scenario.
 *
 *   cd mobile && node --test tests/screenScenarioComposition.test.mjs
 *
 * **Why a source guard when `api.ts` already canonicalises.** The canonicaliser
 * in `scopeQuery` and `postRiskAnalysis` makes the *bug* impossible to observe:
 * a chip id reaching a builder is corrected before it reaches the wire. That is
 * the fix, and it is also why these four call sites can now be wrong without any
 * test failing — which is exactly the state the last review found them in.
 *
 * So this file pins the thing the canonicaliser cannot: **what the screens
 * hold**. A bare `chipId` at a request boundary means the screen is doing the
 * mapping by hand at one of eight places, and the mapping should be done once.
 * The one legal appearance of `chipId` at that boundary is as the argument *to*
 * `scenarioForChip`.
 *
 * Checked as text, in the spirit of `webBundleSafety.test.mjs`: the alternative
 * is rendering the screen, and this repository has no component-rendering
 * harness — which is exactly how the four un-mapped call sites survived to ship.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOBILE = resolve(HERE, '..');

/** Both screens ship to different runtimes; both had the defect. */
const SCREENS = ['components/MapScreen.tsx', 'components/MapScreen.web.tsx'];

/** Every builder that takes a scenario, however it is spelled. */
const BUILDERS = [
  'getExposure',
  'getRoutes',
  'getAllocation',
  'getComparison',
  'postRiskAnalysis',
];

const read = (rel) => readFileSync(resolve(MOBILE, rel), 'utf8');

/**
 * The argument text of every `name(` call in `src`.
 *
 * A balanced-delimiter scan rather than a regex, because these calls span
 * several lines and nest braces (`postRiskAnalysis({ ... })`). A regex would
 * either stop at the first line break or match a mention inside a comment.
 */
function callArguments(src, name) {
  const found = [];
  const re = new RegExp(`\\b${name}\\s*\\(`, 'g');
  let hit;
  while ((hit = re.exec(src)) !== null) {
    let depth = 0;
    let end = hit.index + hit[0].length - 1;
    for (; end < src.length; end += 1) {
      const ch = src[end];
      if (ch === '(' || ch === '{' || ch === '[') depth += 1;
      else if (ch === ')' || ch === '}' || ch === ']') {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    found.push(src.slice(hit.index + hit[0].length, end));
    re.lastIndex = end;
  }
  return found;
}

/** Strip `//` and block comments so a mention in prose is not a call site. */
const withoutComments = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

describe('the screens hold chip ids, and hand the wire a scenario', () => {
  for (const screen of SCREENS) {
    it(`${screen} never passes a bare chipId to a request builder`, () => {
      const src = withoutComments(read(screen));
      const offenders = [];
      for (const builder of BUILDERS) {
        for (const args of callArguments(src, builder)) {
          for (const hit of args.matchAll(/\bchipId\b/g)) {
            const before = args.slice(0, hit.index);
            // Legal only as the argument to the mapping function.
            if (before.endsWith('scenarioForChip(')) continue;
            offenders.push(`${builder}(… ${args.replace(/\s+/g, ' ').trim()} …)`);
          }
        }
      }
      assert.deepEqual(
        offenders,
        [],
        `${screen} hands a chip id straight to the wire:\n  ${offenders.join('\n  ')}`,
      );
    });

    it(`${screen} reaches the mapping through the exported helper`, () => {
      const src = withoutComments(read(screen));
      assert.ok(
        // Both spellings are in use in this tree: the screens import
        // `'../scenarioCompare'` (resolved by Metro), the test files import
        // `'../scenarioCompare.ts'` (resolved by Node). Either is fine.
        /import\s*\{[^}]*\bscenarioForChip\b[^}]*\}\s*from\s*'\.\.\/scenarioCompare(\.ts)?'/.test(
          src,
        ),
        `${screen} does not import scenarioForChip, so a chip-to-wire rename would break it silently`,
      );
    });
  }

  it('the guard actually inspects the four call sites it claims to', () => {
    // A screen that stops calling these builders would make the guard above
    // vacuously true. Count the sites instead of trusting the file to change.
    let exposure = 0;
    let risk = 0;
    for (const screen of SCREENS) {
      const src = withoutComments(read(screen));
      exposure += callArguments(src, 'getExposure').length;
      risk += callArguments(src, 'postRiskAnalysis').length;
    }
    assert.equal(exposure, 2, 'expected one getExposure call per screen');
    assert.equal(risk, 2, 'expected one postRiskAnalysis call per screen');
  });
});