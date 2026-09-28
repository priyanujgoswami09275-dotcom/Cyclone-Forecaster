/**
 * One check for the track waypoint label, run with the stdlib test runner:
 *
 *   node --test 'mobile/tests/*.test.mjs'
 *
 * `api.ts` imports nothing from react-native, which is what makes this
 * possible without a Jest/React-Native rig — the property the file's own
 * header claims, and this is the thing that verifies it.
 *
 * Plain `.mjs` on purpose: the app's `tsconfig.json` includes `**\/*.ts`, and
 * type-checking this file would mean adding `types: ["node"]` and
 * `allowImportingTsExtensions` to the config the app itself builds with. Node
 * strips the types off `api.ts` when it imports it, so the cost of that config
 * change buys nothing here.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { waypointTitle, waypointSubtitle } from '../api.ts';

const REPORTED = {
  sequence: 0,
  timestamp: '2024-05-25T12:00:00Z',
  latitude: 18.8,
  longitude: 89.4,
  wind_kt: 54,
  wind_kmph: 100,
  wind_reported: true,
};

const UNREPORTED = {
  sequence: 9,
  timestamp: '2024-05-27T03:00:00Z',
  latitude: 22.72,
  longitude: 89.07,
  wind_kt: null,
  wind_kmph: null,
  wind_reported: false,
};

test('the title reads the UTC string, never a local-timezone Date', () => {
  assert.equal(waypointTitle(REPORTED), '25 May 2024 · 12:00 UTC');
});

test('the title does not depend on the device timezone', () => {
  // A `new Date(...)` + toLocaleString() implementation shifts this label on a
  // phone east or west of UTC — 12:00Z reads as 17:30 in Kolkata — so the app
  // would report a different time for the same real fix depending on who was
  // looking. Verified separately: under TZ=Asia/Kolkata a Date-based label
  // does read 17.
  const previous = process.env.TZ;
  process.env.TZ = 'Asia/Kolkata';
  assert.equal(waypointTitle(REPORTED), '25 May 2024 · 12:00 UTC');
  process.env.TZ = 'America/Los_Angeles';
  assert.equal(waypointTitle(REPORTED), '25 May 2024 · 12:00 UTC');
  process.env.TZ = previous;
});

test('a reported fix shows the wind in both units the endpoint sent', () => {
  assert.equal(waypointSubtitle(REPORTED), '100 kmph (54 kt)');
});

test('an unreported fix says so and never reads as calm', () => {
  // The committed file writes a blank USA_WIND as 0.0, so "0 kmph" here would
  // be an invented measurement on a map of a real cyclone.
  const subtitle = waypointSubtitle(UNREPORTED);
  assert.match(subtitle, /not reported/i);
  assert.doesNotMatch(subtitle, /0 kmph|0 kt/);
});
