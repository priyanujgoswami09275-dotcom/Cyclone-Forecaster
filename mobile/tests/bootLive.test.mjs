/**
 * Boot must reach 'ready' no matter what the live feed does.
 *
 *   node --test tests/bootLive.test.mjs
 *
 * The boot path runs the four *local* catalogue getters and then never blocks
 * on the live-cyclone call: a rejected live call and a live call that never
 * returns must both produce `ready`. The feed settles separately into its real
 * state, and a failure is the truthful `live_unavailable` — never the boot
 * error screen.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

process.env.EXPO_PUBLIC_API_URL = 'http://unit.test';

const { bootMap, liveUnavailableFromError } = await import('../bootLive.ts');
const { ApiError } = await import('../api.ts');

const CATEGORIES = { categories: [], presets: [] };
const OVERLAYS = { overlays: [] };
const LOCALITIES = { localities: [] };
const CYCLONES = { cyclones: [], default_cyclone_id: 'X' };
const OK = () => ({
  getCategories: async () => CATEGORIES,
  getOverlays: async () => OVERLAYS,
  getLocalities: async () => LOCALITIES,
  getCyclones: async () => CYCLONES,
});

const settleWatching = async (promise, ms = 30) =>
  Promise.race([
    promise.then(() => 'settled', () => 'settled'),
    new Promise((resolve) => setTimeout(() => resolve('pending'), ms)),
  ]);

test('boot reaches ready when the live call rejects', async () => {
  const outcome = await bootMap({
    ...OK(),
    getLiveCyclone: () =>
      Promise.reject(
        new ApiError({ kind: 'network', status: 0, message: 'server unreachable' }),
      ),
  });

  assert.equal(outcome.kind, 'ready');
  // The live call still settles, but into the truthful unavailable state.
  const live = await outcome.livePromise;
  assert.equal(live.status, 'live_unavailable');
  assert.match(live.reason, /server unreachable/);
});

test('boot reaches ready when the live call never resolves', async () => {
  const outcome = await bootMap({
    ...OK(),
    getLiveCyclone: () => new Promise(() => undefined),
  });

  assert.equal(outcome.kind, 'ready');
  // ...and it did not wait on the live feed: the live promise is still pending.
  assert.equal(await settleWatching(outcome.livePromise, 30), 'pending');
});

test('a live call that eventually hangs never corrupts boot', async () => {
  const outcomePromise = bootMap({
    ...OK(),
    getLiveCyclone: async () => {
      await new Promise((r) => setTimeout(r, 40));
      return { status: 'no_active_storm', checked_at: '2026-10-06T00:00:00Z' };
    },
  });
  const outcome = await outcomePromise;
  assert.equal(outcome.kind, 'ready');
});

test('a timeout is surfaced as the real reason, not a boot failure', async () => {
  const timeout = new ApiError({
    kind: 'timeout',
    status: 0,
    message: 'No response from the backend within 150s.',
  });
  const live = liveUnavailableFromError(timeout);
  assert.equal(live.status, 'live_unavailable');
  assert.match(live.reason, /No response from the backend within 150s/);
});
