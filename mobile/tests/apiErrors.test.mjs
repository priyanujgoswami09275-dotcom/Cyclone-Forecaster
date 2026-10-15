/**
 * The status -> ApiErrorKind mapping, run with the stdlib test runner:
 *
 *   node --test 'mobile/tests/*.test.mjs'
 *
 * `api.ts` imports nothing from react-native, which is what makes this
 * possible without a Jest/React-Native rig — the property the file's own
 * header claims, and this is the thing that verifies it.
 *
 * Why the mapping needs its own tests: every kind in `ApiErrorKind` drives a
 * different message and a different available action in the UI. A kind that
 * is never reached still typechecks, still compiles, and still renders — it
 * just tells the user something false. That is the failure mode this file
 * guards, and the 429 -> 'quota' mapping is the one that was actually wrong
 * before it was tested.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

// `request()` reads EXPO_PUBLIC_API_URL at module load, and ESM hoists static
// imports above any assignment — so the import below is dynamic, and the env
// line above it is load-bearing rather than a style choice. Every test in
// this file that does not stub fetch still works: they call `toApiError`
// directly, which never reads the base URL.
process.env.EXPO_PUBLIC_API_URL = 'http://api.test';
const { ApiError, asApiError, request, toApiError } = await import('../api.ts');

/** A minimal Response stand-in: only what `toApiError` actually reads. */
function responseWith(status, { detail = 'detail text', headers = {} } = {}) {
  const lower = new Map(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    status,
    headers: { get: (name) => lower.get(name.toLowerCase()) ?? null },
    json: async () => ({ detail }),
  };
}

test('429 is quota, not capacity and not http', async () => {
  const error = await toApiError(responseWith(429, { detail: 'daily limit reached' }));
  assert.equal(error.kind, 'quota');
  assert.equal(error.status, 429);
});

test('a quota error never carries a retry countdown', async () => {
  // The whole point of separating this from capacity: a client honouring a
  // retry hint for a spent daily limit would poll until midnight Pacific.
  const withoutHeader = await toApiError(responseWith(429));
  assert.equal(withoutHeader.retryAfterSeconds, null);

  // Even if a proxy or platform adds one, the mapping drops it.
  const withHeader = await toApiError(
    responseWith(429, { headers: { 'Retry-After': '60' } }),
  );
  assert.equal(withHeader.retryAfterSeconds, null);
});

test('the quota message says the limit resets, and does not invite a retry', async () => {
  const error = await toApiError(responseWith(429));
  const message = error.message.toLowerCase();
  // "today's" is the client-side phrasing; the backend's `detail` says
  // "daily". Either satisfies "this is a per-day limit, not a blip".
  assert.match(message, /today|daily/);
  assert.match(message, /midnight pacific/);
  assert.doesNotMatch(message, /try again in|usually works in a minute/);
});

test('503 is still capacity and still carries Retry-After', async () => {
  const error = await toApiError(
    responseWith(503, { headers: { 'Retry-After': '60' } }),
  );
  assert.equal(error.kind, 'capacity');
  assert.equal(error.retryAfterSeconds, 60);
});

test('quota and capacity never resolve to the same kind', async () => {
  // The regression: 429 used to fall through to `upstream`/`http`, which
  // reads as a broken app rather than a spent limit.
  const quota = await toApiError(responseWith(429));
  const capacity = await toApiError(responseWith(503));
  assert.notEqual(quota.kind, capacity.kind);
});

test('502 carrying violations is validation', async () => {
  const error = await toApiError(
    responseWith(502, {
      detail: { message: 'withheld', violations: ['invented a locality'] },
    }),
  );
  assert.equal(error.kind, 'validation');
  assert.deepEqual(error.violations, ['invented a locality']);
});

test('502 without violations is upstream', async () => {
  const error = await toApiError(responseWith(502, { detail: 'upstream died' }));
  assert.equal(error.kind, 'upstream');
  assert.deepEqual(error.violations, []);
});

test('an unrecognised status falls through to http', async () => {
  const error = await toApiError(responseWith(418, { detail: 'teapot' }));
  assert.equal(error.kind, 'http');
  assert.equal(error.status, 418);
});

test('a Retry-After that is not a number is dropped, not passed through', async () => {
  // An HTTP-date Retry-After would otherwise become NaN and render as a
  // countdown of "NaNs left".
  const error = await toApiError(
    responseWith(503, { headers: { 'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT' } }),
  );
  assert.equal(error.kind, 'capacity');
  assert.equal(error.retryAfterSeconds, null);
});

test('ApiError defaults are safe when a caller omits the optional fields', () => {
  const error = new ApiError({ kind: 'http', status: 500, message: 'boom' });
  assert.equal(error.retryAfterSeconds, null);
  assert.deepEqual(error.violations, []);
  assert.equal(error.detail, 'boom');
  assert.ok(error instanceof Error);
});

// ---------------------------------------------------------------------------
// The 2xx body, and what can escape the taxonomy.
//
// `request()` used to clear its abort timer the moment the response HEADERS
// arrived, so a body that arrived slowly was never aborted and a body that
// did not parse crashed the caller as a raw SyntaxError. Both are now
// classified errors instead — the UI never sees a non-ApiError from a
// completed HTTP exchange.
// ---------------------------------------------------------------------------

/** Replace global fetch for one call; restore it whatever happens. */
async function withFetch(stub, run) {
  const saved = globalThis.fetch;
  globalThis.fetch = stub;
  try {
    await run();
  } finally {
    globalThis.fetch = saved;
  }
}

test('a 2xx body that does not parse is an upstream error, not a raw crash', async () => {
  await withFetch(
    async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => {
        throw new SyntaxError('Unexpected token < in JSON');
      },
    }),
    async () => {
      await assert.rejects(
        request('/advisory?category=6', { method: 'POST' }, 1000),
        (err) =>
          err instanceof ApiError &&
          err.kind === 'upstream' &&
          err.status === 200 &&
          /unreadable/i.test(err.message),
      );
    },
  );
});

test('the abort timer covers the body download, not just the headers', async () => {
  // fetch answers instantly with a body that never finishes arriving. The
  // timer must still fire while the body is being read, and the rejection
  // must read as a timeout — the taxonomy's word for "we gave up waiting".
  await withFetch(
    async (_url, init) => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: () =>
        new Promise((_, reject) => {
          init.signal.addEventListener('abort', () => {
            const e = new Error('aborted');
            e.name = 'AbortError';
            reject(e);
          });
        }),
    }),
    async () => {
      const pending = request('/surge-zone?category=6', { method: 'GET' }, 40);
      const never = new Promise((_, fail) =>
        setTimeout(
          () => fail(new Error('the timer never fired — it was cleared before the body was read')),
          1500,
        ),
      );
      await assert.rejects(
        Promise.race([pending, never]),
        (err) => err instanceof ApiError && err.kind === 'timeout',
      );
    },
  );
});

test('asApiError passes an ApiError through untouched', () => {
  const original = new ApiError({ kind: 'quota', status: 429, message: 'spent' });
  assert.equal(asApiError(original), original);
});

test('asApiError turns a plain Error into a network error', () => {
  const error = asApiError(new TypeError('fetch failed'));
  assert.ok(error instanceof ApiError);
  assert.equal(error.kind, 'network');
  assert.equal(error.status, 0);
  assert.match(error.message, /fetch failed/);
});

test('asApiError turns a thrown status into an http error', () => {
  const error = asApiError({ status: 502, message: 'bad gateway' });
  assert.ok(error instanceof ApiError);
  assert.equal(error.kind, 'http');
  assert.equal(error.status, 502);
});

test('asApiError stringifies a rejection reason that is not an Error', () => {
  const error = asApiError('the request died');
  assert.ok(error instanceof ApiError);
  assert.equal(error.kind, 'network');
  assert.equal(error.status, 0);
  assert.match(error.message, /the request died/);
});
