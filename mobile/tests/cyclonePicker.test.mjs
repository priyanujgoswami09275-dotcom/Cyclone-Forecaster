/**
 * The picker offers "Live" only when the live path can lead somewhere.
 *
 *   cd mobile && node --test tests/cyclonePicker.test.mjs
 *
 * **Why a source guard.** Rendering `CyclonePicker` needs JSX plus a
 * `react-native` host, and this repository has no component-rendering
 * harness — the same reason `liveLoading.test.mjs` and
 * `screenScenarioComposition.test.mjs` read their files as text. What is
 * pinned here is one conditional and one effect, and the failure mode is
 * *presence* — which a text assertion expresses directly.
 *
 * **The rule.** A toggle whose every outcome is "no data" is not an
 * option: when the feed resolved `live_unavailable`, the Live chip is not
 * offered, and a mode stranded on Live by toggling during the probe
 * returns to the historical list. Every other state keeps the option —
 * a probe in flight is about to answer, and `no_active_storm` is an
 * honest state a reader can act on.
 *
 * This is shared picker logic, not a Web-only presentation choice, so it
 * holds on both screens; `plainLiveNote` is the Web-only banner prop.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOBILE = resolve(HERE, '..');

const read = (rel) => readFileSync(resolve(MOBILE, rel), 'utf8');

/** Strip `//` and block comments so prose cannot satisfy a guard. */
const withoutComments = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

describe('the Live option is offered only when it can lead somewhere', () => {
  const src = withoutComments(read('components/CyclonePicker.tsx'));

  it('the option is removed exactly when the feed resolved unavailable', () => {
    assert.match(
      src,
      /liveOffered\s*=\s*live\?\.status\s*!==\s*'live_unavailable'/,
      'the Live option is no longer gated on live_unavailable — a toggle ' +
        'whose only outcome is "no data" is back',
    );
  });

  it('Historical is never removable — the list is the fallback, not a mode', () => {
    // The offered pair always contains historical; only 'live' is
    // conditional. If this guard fails, someone made the list itself
    // conditional on the live state, which would strand every reader the
    // moment the feed is down.
    assert.match(
      src,
      /\(\[\s*['"]historical['"],\s*['"]live['"]\]\s*as const/,
      'the offered pair no longer starts with the historical list',
    );
    assert.ok(
      src.includes("(['historical'] as const)"),
      'the fallback array no longer keeps the historical option unconditionally',
    );
  });

  it('a mode stranded on Live by the probe resolving down returns to the list', () => {
    assert.match(
      src,
      /live\?\.status === 'live_unavailable' && mode === 'live'[\s\S]{0,80}setMode\('historical'\)/,
      'the snap-back from a vanished Live mode is gone — a reader can be ' +
        'stranded on a mode with no option',
    );
  });
});
