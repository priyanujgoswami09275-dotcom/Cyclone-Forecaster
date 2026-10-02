/**
 * The Dynamic Cyclone System's request routing.
 *
 *   cd mobile && node --test tests/apiCyclones.test.mjs
 *
 * **Why this file exists.** `GET /track` declares `def get_track() -> dict` —
 * no parameters. FastAPI silently ignores an unrecognised query string, so a
 * client that "scoped" `/track` by adding `?cyclone_id=…` received a 200 and
 * Cyclone Remal's track for *every* id, including `nonexistent`. The request
 * looked correct, the response looked correct, and the map quietly showed the
 * wrong storm's track after a switch.
 *
 * That defect shipped and was found only by driving the app in a browser, so
 * the routing is asserted here: an un-scoped request must be byte-identical to
 * the URL the app has always sent, and a scoped one must hit the endpoint that
 * actually accepts the id.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

// Must be set before `api.ts` is imported — it reads the env at module load.
process.env.EXPO_PUBLIC_API_URL = 'http://unit.test';

const { getAllocation, getExposure, getRoutes, getTrack } = await import('../api.ts');
const {
  getComparison,
  getCyclones,
  getCycloneTrack,
  getLiveCyclone,
  getScenarios,
  postRiskAnalysis,
} = await import('../apiCyclones.ts');

/** Every URL the app asked for, in order. */
const calls = [];
const originalFetch = globalThis.fetch;

// `beforeEach`, not a one-shot install: restoring the real `fetch` after each
// test would send tests 2+ to the network, where they fail for a reason that
// has nothing to do with the routing under test.
test.beforeEach(() => {
  calls.length = 0;
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return {
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({}),
    };
  };
});

test.afterEach(() => {
  globalThis.fetch = originalFetch;
});

const last = () => calls[calls.length - 1].url;
const run = async (fn) => {
  calls.length = 0;
  await fn();
  return last();
};

// ---------------------------------------------------------------------------
// The defect this file exists for.
// ---------------------------------------------------------------------------

test('an un-scoped getTrack hits /track, exactly as it always has', async () => {
  assert.equal(await run(() => getTrack()), 'http://unit.test/track');
});

test('a scoped getTrack hits /cyclones/{id}/track, not /track?cyclone_id=', async () => {
  // The old URL, for the record: `http://unit.test/track?cyclone_id=…`
  // — 200 OK, Remal's track, whatever the id was.
  const url = await run(() => getTrack('1970324N05143'));
  assert.equal(url, 'http://unit.test/cyclones/1970324N05143/track');
  assert.ok(
    !url.includes('cyclone_id='),
    `the case-study endpoint ignores cyclone_id — ${url} would silently return Remal`,
  );
});

test('getCycloneTrack is the same route as a scoped getTrack', async () => {
  // One URL-building site. Two would be two chances for them to disagree, and
  // only one of them would be the one a screen calls.
  const a = await run(() => getCycloneTrack('X'));
  const b = await run(() => getTrack('X'));
  assert.equal(a, b);
});

// ---------------------------------------------------------------------------
// Un-scoped requests must be byte-identical to the pre-Dynamic-System URLs.
// A captured fixture stays a fixture only if the query string does not move.
// ---------------------------------------------------------------------------

test('un-scoped exposure, routes and allocation URLs are unchanged', async () => {
  assert.equal(await run(() => getExposure(6)), 'http://unit.test/exposure?category=6');
  assert.equal(
    await run(() => getRoutes(6, 'kakdwip')),
    'http://unit.test/routes?category=6&origin=kakdwip',
  );
  assert.equal(
    await run(() => getAllocation(6, 'kakdwip')),
    'http://unit.test/allocation?category=6&origin=kakdwip',
  );
});

test('scoped requests append cyclone_id and scenario_id', async () => {
  assert.equal(
    await run(() => getExposure(6, 'A', 'cat5')),
    'http://unit.test/exposure?category=6&cyclone_id=A&scenario_id=cat5',
  );
  assert.equal(
    await run(() => getRoutes(6, 'kakdwip', 'A', 'cat5')),
    'http://unit.test/routes?category=6&origin=kakdwip&cyclone_id=A&scenario_id=cat5',
  );
});

test('an id containing reserved characters is encoded', async () => {
  const url = await run(() => getTrack('a/b c'));
  assert.ok(url.includes('a%2Fb%20c'), url);
  assert.ok(!url.includes('a/b c'), url);
});

// ---------------------------------------------------------------------------
// The six Dynamic Cyclone System endpoints.
// ---------------------------------------------------------------------------

test('getCyclones and getLiveCyclone hit their own routes', async () => {
  assert.equal(await run(() => getCyclones()), 'http://unit.test/cyclones');
  assert.equal(await run(() => getLiveCyclone()), 'http://unit.test/live-cyclone');
});

test('getScenarios passes cyclone_id as a query parameter', async () => {
  assert.equal(
    await run(() => getScenarios('A')),
    'http://unit.test/scenarios?cyclone_id=A',
  );
});

test('getComparison carries the ids and the scenario it was asked for', async () => {
  assert.equal(
    await run(() => getComparison(['A', 'B'], 6, 'cat5')),
    'http://unit.test/comparison?category=6&cyclone_ids=A,B&scenario_id=cat5',
  );
});

test('getComparison omits scenario_id when not given, keeping the default', async () => {
  assert.equal(
    await run(() => getComparison(['A'], 6)),
    'http://unit.test/comparison?category=6&cyclone_ids=A',
  );
});

test('postRiskAnalysis is a POST with a JSON body', async () => {
  const url = await run(() => postRiskAnalysis({ category: 6, cyclone_id: 'A' }));
  assert.equal(url, 'http://unit.test/risk-analyst');
  const init = calls[calls.length - 1].init;
  assert.equal(init.method, 'POST');
  assert.equal(init.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(init.body), { category: 6, cyclone_id: 'A' });
});
