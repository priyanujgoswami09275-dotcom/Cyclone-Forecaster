/**
 * A pending live probe must look pending, not empty.
 *
 *   cd mobile && node --test tests/liveLoading.test.mjs
 *
 * **The defect.** Decoupling boot from the live feed (`bootLive.ts`) left
 * `liveLoading` initialised to `false` and never set again. Nothing turned it
 * on, so while the probe was in flight the picker held `live === null` *and*
 * `liveLoading === false`, and its live panel fell through every branch to
 * `null` — an empty slot. The honest state, "asked, still waiting", had a
 * branch written for it (`liveLoading ? <ActivityIndicator/>`) and no way to be
 * reached.
 *
 * **Why these are source guards and not component tests.** Rendering
 * `CyclonePicker` needs JSX plus a `react-native` host, and this repository has
 * no component-rendering harness (the same reason
 * `screenScenarioComposition.test.mjs` reads its files as text). What is being
 * pinned is a ternary chain and two `useState` calls in three files, and the
 * failure mode of the chain is *ordering* — which is exactly what a text
 * assertion can express and a render would only express indirectly.
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
const PICKER = 'components/CyclonePicker.tsx';

const read = (rel) => readFileSync(resolve(MOBILE, rel), 'utf8');

/** Strip `//` and block comments so prose cannot satisfy or break a guard. */
const withoutComments = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

/**
 * The text between `needle` and its balanced closer.
 *
 * A balanced-delimiter scan rather than a regex, because these blocks span
 * several lines. Only `openChar`/`closeChar` are counted — JSX attribute braces
 * inside a parenthesised branch are not part of the paren nesting, so counting
 * every delimiter type at once overcounts and never closes.
 */
function block(src, needle, openChar, closeChar) {
  const at = src.indexOf(needle);
  assert.notEqual(at, -1, `no ${JSON.stringify(needle)} in source`);
  let depth = 0;
  for (let i = at + needle.length - 1; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === openChar) depth += 1;
    else if (ch === closeChar) {
      depth -= 1;
      if (depth === 0) return src.slice(at + needle.length, i);
    }
  }
  throw new Error(`unbalanced ${JSON.stringify(needle)}`);
}

/** The text inside the `.then(` that follows `livePromise`. */
function liveThen(src) {
  const at = src.indexOf('livePromise.then');
  assert.notEqual(at, -1, 'the boot effect no longer branches on livePromise');
  return block(src.slice(at), '.then(', '(', ')');
}

/**
 * The boot effect's ready path: from the `bootMap` call up to (not including)
 * the branch on `livePromise`.
 */
function readyPath(src) {
  const from = src.indexOf('await bootMap(');
  assert.notEqual(from, -1, 'the screen no longer boots through bootMap');
  const to = src.indexOf('livePromise.then');
  assert.notEqual(to, -1, 'the boot effect no longer branches on livePromise');
  assert.ok(from < to, 'the live probe is now branched before boot resolves');
  return src.slice(from, to);
}

describe('a pending live probe is announced as pending', () => {
  for (const screen of SCREENS) {
    it(`${screen} raises liveLoading between ready and the live branch`, () => {
      const path = readyPath(withoutComments(read(screen)));
      const readyAt = path.indexOf("status: 'ready'");
      assert.notEqual(readyAt, -1, 'boot no longer reaches ready');
      const loadingAt = path.indexOf('setLiveLoading(true)');
      assert.notEqual(
        loadingAt,
        -1,
        'never calls setLiveLoading(true) on the ready path, so a pending probe renders as an empty slot',
      );
      assert.ok(
        loadingAt > readyAt,
        'sets liveLoading before boot is ready — it would claim a probe that never started',
      );
    });

    it(`${screen} lowers liveLoading when the probe settles`, () => {
      const src = withoutComments(read(screen));
      const then = liveThen(src);
      assert.match(
        then,
        /setLiveLoading\(false\)/,
        'never clears liveLoading — the spinner would outlive the answer',
      );
      assert.ok(
        then.indexOf('setLiveLoading(false)') < then.indexOf('setLive('),
        'clears liveLoading after committing the answer, so one render shows neither spinner nor live state',
      );
    });

    it(`${screen} passes liveLoading to the picker`, () => {
      const src = withoutComments(read(screen));
      const pickerAt = src.indexOf('<CyclonePicker');
      assert.notEqual(pickerAt, -1, `${screen} does not render CyclonePicker`);
      assert.match(
        src.slice(pickerAt, pickerAt + 800),
        /\bliveLoading=\{liveLoading\}/,
        `${screen} does not hand liveLoading to the picker, so the loading row is unreachable`,
      );
    });
  }
});

describe('the picker shows the loading row, not an empty slot', () => {
  const src = withoutComments(read(PICKER));

  it('liveLoading is a required prop, so no caller can leave it undefined', () => {
    assert.match(
      src,
      /liveLoading:\s*boolean;/,
      'CyclonePickerProps must require liveLoading — an optional flag is an empty slot waiting to happen',
    );
  });

  it('the liveLoading branch precedes the live branch', () => {
    // In a `… : liveLoading ? A : live ? B : null` chain, ordering *is* the
    // behaviour: with `live === null` and `liveLoading === true` only the first
    // matching branch renders. Swapping the two silently restores the empty slot.
    const loadingAt = src.indexOf(': liveLoading ? (');
    const liveAt = src.indexOf(': live ? (');
    assert.notEqual(loadingAt, -1, 'the liveLoading branch is gone from the picker');
    assert.notEqual(liveAt, -1, 'the live branch is gone from the picker');
    assert.ok(
      loadingAt < liveAt,
      'the live branch now precedes liveLoading, so a pending probe falls through to null',
    );
  });

  it('the empty slot is the live branch’s null arm, past both branches', () => {
    const tail = src.slice(src.indexOf(': live ? ('));
    assert.match(
      tail,
      /\)\s*:\s*null\s*\}/,
      'the live branch no longer falls back to null — behaviour changed, recheck this guard',
    );
  });

  it('the loading row says what it is, rather than showing a bare spinner', () => {
    const loading = block(
      src.slice(src.indexOf(': liveLoading ? (')),
      '? (',
      '(',
      ')',
    );
    assert.match(loading, /ActivityIndicator/, 'the loading row has no spinner');
    assert.match(
      loading,
      /Checking the live feed/,
      'the loading row does not say what is being waited on',
    );
    assert.doesNotMatch(
      loading,
      /\?\s*[\s\S]*\)\s*:\s*live\s*\?/,
      'the loading row contains a nested chain, so the branch order above is not the last word',
    );
  });
});
