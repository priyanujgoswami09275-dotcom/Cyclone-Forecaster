/**
 * postAdvisory must attach cyclone_id and scenario_id — the chip's stable UI
 * id translated the same way the other builders translate it, so the "Remal"
 * chip reaches the wire as `scenario_id=observed`, never as the UI token
 * `remal_observed`.
 *
 *   node --test tests/postAdvisoryUrl.test.mjs
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

process.env.EXPO_PUBLIC_API_URL = 'http://unit.test';

const { postAdvisory } = await import('../api.ts');
const { scenarioForChip } = await import('../scenarioCompare.ts');
const { STRENGTH_CHIPS } = await import('../strengthChips.ts');

const calls = [];
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), init });
  return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
};

const run = async (fn) => {
  calls.length = 0;
  await fn();
  return calls[calls.length - 1];
};

test('the Remal chip asks for scenario_id=observed, not scenario_id=remal_observed', async () => {
  const chipId = STRENGTH_CHIPS.find((chip) => chip.id === 'remal_observed').id;
  const call = await run(() =>
    postAdvisory(3, 'sagar', '2024145N14087', scenarioForChip(chipId)),
  );
  assert.equal(call.url, 'http://unit.test/advisory?category=3&origin=sagar&cyclone_id=2024145N14087&scenario_id=observed');
  assert.ok(!call.url.includes('scenario_id=remal_observed'), call.url);
});

test('a chip with no storm overrides the default through the same scope', async () => {
  const band = scenarioForChip('cat6');
  assert.equal(band, 'cat6');
  const call = await run(() => postAdvisory(6, 'sagar', '2024145N14087', band));
  assert.ok(call.url.includes('scenario_id=cat6'));
  assert.ok(call.url.includes('cyclone_id=2024145N14087'));
});
