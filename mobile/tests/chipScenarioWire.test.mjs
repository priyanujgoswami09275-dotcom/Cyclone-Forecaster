/**
 * The chip id is not the wire id, and the gap shipped a 400.
 *
 *   cd mobile && node --test tests/chipScenarioWire.test.mjs
 *
 * **The defect.** The case-study chip is keyed `remal_observed`. The backend
 * registers that scenario as `observed`. The four request builders that carried
 * a `scenario_id` were handed the chip id verbatim, so selecting the case-study
 * chip produced
 *
 *     GET  /exposure?category=3&scenario_id=remal_observed   -> 400
 *     POST /risk-analyst          { "scenario_id": "remal_observed" } -> 400
 *
 * `/exposure`'s 400 then rendered as *"Nothing is exposed at this strength"* —
 * a confident claim about a request that never ran. `scenarioForChip` existed
 * and was correct; it was called from `onOpenCompare` and from nowhere else.
 *
 * **Why these are URL-and-body assertions and not component tests.** The bug
 * lived in the *composition* — a chip id reaching a request — and the layer that
 * owns that composition is the request builder. Driving a React screen would
 * pin one call site; pinning the builder pins every present and future caller,
 * including the ones that never existed when this was written. The four call
 * sites are additionally pinned by `screenScenarioComposition.test.mjs`.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

// Must be set before `api.ts` is imported — it reads the env at module load.
process.env.EXPO_PUBLIC_API_URL = 'http://unit.test';

const { getAllocation, getExposure, getRoutes } = await import('../api.ts');
const { getComparison, postRiskAnalysis } = await import('../apiCyclones.ts');
const { wireScenarioId } = await import('../cycloneModel.ts');
const { scenarioForChip } = await import('../scenarioCompare.ts');
const { DEFAULT_CHIP } = await import('../strengthChips.ts');

// ---------------------------------------------------------------------------
// Request capture
// ---------------------------------------------------------------------------

/** Every `ChipId` the app can offer, plus what the wire calls it. */
const CHIPS = [
  ['remal_observed', 'observed'],
  ['cat4', 'cat4'],
  ['cat5', 'cat5'],
  ['cat6', 'cat6'],
];

const calls = [];
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), init });
  return {
    ok: true,
    status: 200,
    json: async () => ({}),
    text: async () => '{}',
  };
};

const run = async (fn) => {
  calls.length = 0;
  await fn();
  return calls[calls.length - 1];
};

/** The `scenario_id` on a request URL, or `null` when it carries none. */
const scenarioOn = (url) => {
  const hit = /[?&]scenario_id=([^&]*)/.exec(url);
  return hit === null ? null : decodeURIComponent(hit[1]);
};

// ---------------------------------------------------------------------------
// The mapping itself
// ---------------------------------------------------------------------------

test('the case-study chip id becomes the backend spelling, and nothing else does', () => {
  assert.equal(wireScenarioId('remal_observed'), 'observed');
  // Bands are already wire ids — unchanged, which is what keeps an existing
  // captured fixture byte-identical.
  for (const band of ['cat0', 'cat1', 'cat2', 'cat3', 'cat4', 'cat5', 'cat6']) {
    assert.equal(wireScenarioId(band), band);
  }
});

test('an unrecognised id is passed through, so the backend still rejects it', () => {
  // Fail closed. Mapping everything unknown onto `observed` would silently
  // answer with a storm's own peak wind for a scenario nobody asked for.
  for (const bogus of ['', 'observed', 'cat9', 'REMAL_OBSERVED', 'Observed', 'nonsense']) {
    assert.equal(wireScenarioId(bogus), bogus, `${JSON.stringify(bogus)} was rewritten`);
  }
});

test('every chip the app offers maps to a scenario the backend registers', () => {
  // `scenarioForChip` is what a screen asks; `wireScenarioId` is what the
  // backend sees. They must not be able to disagree.
  for (const [chip, expected] of CHIPS) {
    assert.equal(scenarioForChip(chip), expected);
  }
});

// ---------------------------------------------------------------------------
// /exposure — the request that rendered a failed call as an empty map
// ---------------------------------------------------------------------------

for (const [chip, expected] of CHIPS) {
  test(`getExposure sends scenario_id=${expected} for the ${chip} chip`, async () => {
    // Both spellings are accepted, because the builder owns the mapping and
    // the screen uses the presentation-side helper. Either way the wire is right.
    for (const held of [chip, expected]) {
      const call = await run(() => getExposure(3, '2024145N14087', held));
      assert.equal(scenarioOn(call.url), expected);
      assert.ok(
        !call.url.includes('remal_observed'),
        `${call.url} would come back 400 from /exposure`,
      );
    }
  });
}

// ---------------------------------------------------------------------------
// /routes and /allocation — the same funnel, and an unchanged default URL
// ---------------------------------------------------------------------------

test('getRoutes and getAllocation canonicalise the scenario too', async () => {
  const routes = await run(() => getRoutes(3, 'sagar', '2024145N14087', 'remal_observed'));
  assert.equal(scenarioOn(routes.url), 'observed');
  const allocation = await run(() =>
    getAllocation(3, 'sagar', '2024145N14087', 'remal_observed'),
  );
  assert.equal(scenarioOn(allocation.url), 'observed');
});

test('an unscoped request is byte-identical to the URL it has always sent', () => {
  // Backwards compatibility, asserted as bytes rather than as intent: the
  // canonicaliser must not add a parameter that was not there before.
  assert.equal(scopeOf('http://unit.test/exposure?category=6'), '');
  assert.equal(
    scopeOf('http://unit.test/routes?category=6&origin=sagar'),
    '',
  );
  assert.equal(scopeOf('http://unit.test/allocation?category=6&origin=sagar'), '');
});

/** The `cyclone_id`/`scenario_id` tail of a URL, or `''` when unscoped. */
function scopeOf(url) {
  const hit = /[?&](cyclone_id|scenario_id)=/.exec(url);
  return hit === null ? '' : url.slice(url.indexOf(hit[0]) + 1);
}

// ---------------------------------------------------------------------------
// /comparison and /risk-analyst — the POST body is the wire too
// ---------------------------------------------------------------------------

test('getComparison canonicalises the scenario id', async () => {
  const call = await run(() =>
    getComparison(['2024145N14087', '1970324N05143'], 3, 'remal_observed'),
  );
  assert.equal(scenarioOn(call.url), 'observed');
});

test('postRiskAnalysis sends scenario_id=observed, and does not mutate the caller', async () => {
  // The 400 that motivated all of this: `/risk-analyst` validates `scenario_id`
  // before it reads anything else, so the chip id came back as a bare
  // `400 unknown scenario 'remal_observed'`.
  const body = { category: 6, cyclone_id: '2024145N14087', scenario_id: 'remal_observed' };
  const call = await run(() => postRiskAnalysis(body));
  assert.equal(call.init.method, 'POST');
  assert.equal(JSON.parse(call.init.body).scenario_id, 'observed');
  // The caller's object is the screen's state. Mutating it would make the
  // mapping stick on the next press and hide which chip is actually selected.
  assert.equal(body.scenario_id, 'remal_observed');
});

test('postRiskAnalysis omits scenario_id entirely when there is none', async () => {
  const call = await run(() => postRiskAnalysis({ category: 6 }));
  assert.equal('scenario_id' in JSON.parse(call.init.body), false);
});

// ---------------------------------------------------------------------------
// Cache isolation — the pair, not either half
// ---------------------------------------------------------------------------

test('cyclone and scenario are both carried, so a cached pair cannot be reused', async () => {
  const call = await run(() => getExposure(6, '1970324N05143', 'remal_observed'));
  assert.ok(call.url.includes('cyclone_id=1970324N05143'));
  assert.ok(call.url.includes('scenario_id=observed'));
});

test('the two axes are independent — same storm, different strengths', async () => {
  const seen = new Set();
  for (const chip of ['remal_observed', 'cat5', 'cat6']) {
    const call = await run(() => getExposure(6, '2024145N14087', chip));
    seen.add(`${call.url.match(/cyclone_id=([^&]*)/)[1]}|${scenarioOn(call.url)}`);
  }
  assert.equal(seen.size, 3, `the three chips collapsed onto ${[...seen].join(', ')}`);
});

test('the default chip is a band chip, so the default URL is unscoped', async () => {
  // `DEFAULT_CHIP` is `cat6`. If that ever becomes `remal_observed`, the
  // unscoped-first-boot request would start carrying a scenario, and this
  // fails rather than letting it.
  assert.equal(scenarioForChip(DEFAULT_CHIP), 'cat6');
  const call = await run(() => getExposure(6, undefined, scenarioForChip(DEFAULT_CHIP)));
  assert.equal(scenarioOn(call.url), 'cat6');
});