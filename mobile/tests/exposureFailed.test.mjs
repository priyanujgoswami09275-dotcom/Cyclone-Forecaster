/**
 * A failed exposure request must say "unknown", not show stale zeros.
 *
 *   cd mobile && node --test tests/exposureFailed.test.mjs
 *
 * **The pattern.** Decoupling boot from the live feed taught the same lesson
 * the exposure path had already tripped over: an error branch exists in the
 * render, but nothing ever raises the flag that reaches it. `routes` sets
 * `routesFailed(true)` in its catch (line ~369); `exposure` set only
 * `exposure=null`, so the note at line ~774 was dead code and a failed fetch
 * fell through to the zero-count empty state — "Nothing is exposed" rendered
 * because the request failed, not because the surge found no assets.
 *
 * **Why a source guard:** this repository has no component-rendering harness
 * (the same reason `screenScenarioComposition.test.mjs` reads files as text),
 * and what is pinned is one setState call inside one `.catch` plus an ordering
 * fact in the render chain — exactly what text assertions express well.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOBILE = resolve(HERE, '..');
const read = (rel) => readFileSync(resolve(MOBILE, rel), 'utf8');

const withoutComments = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

const WEB = 'components/MapScreen.web.tsx';

/** The exposure effect: from its marker comment to the routes marker. */
function exposureEffect(src) {
  const from = src.indexOf('--- exposure:');
  assert.notEqual(from, -1, 'the exposure effect marker is gone');
  const to = src.indexOf('--- routes:');
  assert.notEqual(to, -1, 'the routes effect marker is gone');
  assert.ok(from < to, 'markers out of order');
  return src.slice(from, to);
}

describe('exposure failure is surfaced, not rendered as zeros', () => {
  it('the exposure effect raises exposureFailed in its catch', () => {
    const effect = withoutComments(exposureEffect(read(WEB)));
    const catchAt = effect.indexOf('.catch(');
    assert.notEqual(catchAt, -1, 'the exposure fetch has no .catch');
    // The catch literally: `setExposure(null)` and now also the flag.
    const catchBody = effect.slice(catchAt, effect.indexOf('.finally(', catchAt));
    assert.match(
      catchBody,
      /setExposureFailed\(true\)/,
      'a failed exposure fetch never sets exposureFailed, so the "unknown" note is dead code',
    );
    assert.match(
      catchBody,
      /setExposure\(null\)/,
      'the catch must also drop the stale counts — both, not either',
    );
  });

  it('the flag is cleared when a new fetch starts', () => {
    const effect = withoutComments(exposureEffect(read(WEB)));
    const resetAt = effect.indexOf('setExposureFailed(false)');
    const fetchAt = effect.indexOf('getExposure(');
    assert.notEqual(resetAt, -1, 'the effect never resets exposureFailed');
    assert.notEqual(fetchAt, -1, 'the exposure fetch call moved?');
    assert.ok(
      resetAt < fetchAt,
      'exposureFailed is not cleared before the new request starts, so a stale error outlives the retry',
    );
  });

  it('the failed note wins the render chain over the loading note', () => {
    const src = withoutComments(read(WEB));
    const failedAt = src.indexOf('{exposureFailed ? (');
    const loadingAt = src.indexOf(') : exposureLoading ? (');
    assert.notEqual(failedAt, -1, 'the exposureFailed render branch is gone');
    assert.notEqual(loadingAt, -1, 'the exposureLoading render branch is gone');
    assert.ok(
      failedAt < loadingAt,
      'exposureLoading now renders before exposureFailed, so a retry hides the failure note',
    );
  });

  it('state actually exists — the flag is declared and passed nowhere new', () => {
    const src = read(WEB);
    assert.match(
      src,
      /const \[exposureFailed, setExposureFailed\] = useState\(false\)/,
      'exposureFailed state declaration changed unexpectedly',
    );
  });
});
