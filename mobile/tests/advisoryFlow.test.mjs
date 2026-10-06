/**
 * The advisory failure/stale/SMS logic, run with the stdlib test runner:
 *
 *   node --test 'mobile/tests/*.test.mjs'
 *
 * Works because `advisoryFlow.ts` imports nothing from react-native — only
 * *types* from `api.ts`, which are erased at compile time. This file is the
 * thing that verifies that claim rather than asserting it in a comment.
 *
 * What is actually being guarded here: each `ApiErrorKind` produces a
 * different, correct message and a different available action. A kind that is
 * never reached still typechecks, still renders, and still tells the user
 * something false — which is how a spent daily quota came to be reported as
 * "something failed upstream" before it was separated out.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ApiError } from '../api.ts';
import {
  advisoryKey,
  cachedBannerText,
  cachedSampleOffer,
  describeAdvisoryError,
  isAdvisoryStale,
  sheltersAreDemoData,
  smsLengthStatus,
  SMS_LIMIT,
} from '../advisoryFlow.ts';

/** Every kind `api.ts` can produce, so a new one fails this file loudly. */
const ALL_KINDS = [
  'config',
  'timeout',
  'network',
  'capacity',
  'quota',
  'validation',
  'upstream',
  'http',
];

function err(kind, extra = {}) {
  return new ApiError({ kind, status: 500, message: 'boom', ...extra });
}

/** A minimal AdvisoryResponse carrying only what the stale check reads. */
function responseFor(category, originId) {
  return {
    advisory: {
      executive_summary: '',
      evacuation_plan: [],
      sms_dispatch_draft: '',
      post_landfall_risks: '',
      historical_context: '',
    },
    generated_for: {
      category,
      cyclone_id: '2024145N14087',
      scenario_id: `cat${category}`,
      origin: { id: originId, name: originId },
    },
  };
}

function responseForScenario(category, originId, cycloneId, scenarioId) {
  const r = responseFor(category, originId);
  r.generated_for.cyclone_id = cycloneId;
  r.generated_for.scenario_id = scenarioId;
  return r;
}

test('every ApiErrorKind has a branch, and each returns a full failure', () => {
  for (const kind of ALL_KINDS) {
    const failure = describeAdvisoryError(err(kind));
    assert.ok(failure.title.length > 0, `${kind} has no title`);
    assert.ok(failure.body.length > 0, `${kind} has no body`);
    assert.ok(failure.action.type, `${kind} has no action type`);
  }
});

test('every action carries a label, so no failure is a dead end', () => {
  // There is deliberately no "no action" variant in the union. This asserts
  // that stays true: every failure can say at least one useful sentence about
  // what to do next.
  for (const kind of ALL_KINDS) {
    const { action } = describeAdvisoryError(err(kind));
    assert.ok(action.label.length > 0, `${kind} has an action with no label`);
  }
});

test('no two kinds share a title', () => {
  // A copy-paste that gave two failures the same headline would be
  // indistinguishable to the user, which defeats the point of the switch.
  const titles = ALL_KINDS.map((kind) => describeAdvisoryError(err(kind)).title);
  assert.equal(new Set(titles).size, ALL_KINDS.length);
});

test('quota offers no retry, and no countdown', () => {
  // The decision this module exists to enforce. A retry button here invites a
  // user to burn the rest of the day's quota against a limit that has already
  // run out, and a countdown would promise that waiting helps.
  const failure = describeAdvisoryError(err('quota', { status: 429 }));
  assert.notEqual(failure.action.type, 'retry');
  assert.match(failure.action.label, /midnight pacific/i);
  assert.doesNotMatch(failure.action.label, /\d+\s*(s|sec|second|minute)/i);
});

test('quota does not inherit a Retry-After even if one somehow arrives', () => {
  // Belt and braces: `toApiError` already forces this to null, so this
  // asserts the message stays correct if that mapping is ever changed. The
  // thing being guarded is the capacity "wait of about Ns" clause leaking into
  // this branch, so the ban is on any duration rather than that one literal.
  const failure = describeAdvisoryError(err('quota', { status: 429, retryAfterSeconds: 60 }));
  assert.doesNotMatch(failure.body, /\d+\s*s(ec|econd)?\b/);
});

test('capacity offers a retry and quotes the server\'s Retry-After', () => {
  const failure = describeAdvisoryError(err('capacity', { status: 503, retryAfterSeconds: 60 }));
  assert.equal(failure.action.type, 'retry');
  assert.match(failure.body, /60s/);
});

test('capacity with no Retry-After still reads, and does not invent a number', () => {
  const failure = describeAdvisoryError(err('capacity', { status: 503, retryAfterSeconds: null }));
  assert.equal(failure.action.type, 'retry');
  assert.doesNotMatch(failure.body, /wait of about/);
});

test('network blames neither the app nor the server', () => {
  const failure = describeAdvisoryError(err('network', { status: 0 }));
  assert.equal(failure.action.type, 'retry');
  assert.doesNotMatch(failure.body, /broken|bug|crash/i);
});

test('timeout says the quota is already spent, and offers no button', () => {
  // A timeout is the one case where "try again" is actively expensive: the
  // backend may still be working and the call has already been charged.
  const failure = describeAdvisoryError(err('timeout', { status: 0 }));
  assert.notEqual(failure.action.type, 'retry');
  assert.match(failure.body, /spent Gemini quota|already spent/i);
});

test('config is a build problem, so the action is reload rather than retry', () => {
  const failure = describeAdvisoryError(err('config', { status: 0 }));
  assert.equal(failure.action.type, 'reload');
  assert.match(failure.action.label, /EXPO_PUBLIC_API_URL/);
});

test('validation surfaces the violations, and nothing else does', () => {
  const violations = ['named a locality not in the plan', 'quoted a shelter occupancy figure'];
  const failure = describeAdvisoryError(err('validation', { status: 502, violations }));
  assert.deepEqual(failure.violations, violations);

  for (const kind of ALL_KINDS.filter((k) => k !== 'validation')) {
    assert.deepEqual(
      describeAdvisoryError(err(kind)).violations,
      [],
      `${kind} should not carry violations`,
    );
  }
});

test('http reports the status it actually got', () => {
  const failure = describeAdvisoryError(err('http', { status: 418 }));
  assert.match(failure.title, /418/);
});

test('the stale key needs both halves', () => {
  // Category alone misses "right intensity, wrong town"; origin alone misses
  // "right town, wrong storm".
  assert.notEqual(advisoryKey(5, 'sagar'), advisoryKey(6, 'sagar'));
  assert.notEqual(advisoryKey(5, 'sagar'), advisoryKey(5, 'kakdwip'));
  assert.equal(advisoryKey(5, 'sagar'), advisoryKey(5, 'sagar'));
});

test('an advisory for the current scenario is not stale', () => {
  assert.equal(isAdvisoryStale(responseFor(5, 'sagar'), 5, 'sagar'), false);
});

test('moving the slider makes the open advisory stale', () => {
  assert.equal(isAdvisoryStale(responseFor(5, 'sagar'), 6, 'sagar'), true);
});

test('changing the origin makes the open advisory stale', () => {
  assert.equal(isAdvisoryStale(responseFor(5, 'sagar'), 5, 'kakdwip'), true);
});

test('staleness is judged by the server\'s echo, not the request', () => {
  // The response says it was written for 6/sagar. If the screen now shows
  // 5/kakdwip, the prose in hand is for something else entirely.
  assert.equal(isAdvisoryStale(responseFor(6, 'sagar'), 5, 'kakdwip'), true);
});

test('the stale guard fires when the scenario changes at a constant category', () => {
  // Generated for category 6, Remal, observed. The screen stays on category 6
  // and the same town but asks for a band — the open advisory is not the one
  // for what the screen now shows.
  const generatedForRemalObserved = responseForScenario(
    6, 'sagar', '2024145N14087', 'observed',
  );
  assert.equal(
    isAdvisoryStale(generatedForRemalObserved, 6, 'sagar', '2024145N14087', 'observed'),
    false,
  );
  assert.equal(
    isAdvisoryStale(generatedForRemalObserved, 6, 'sagar', '2024145N14087', 'cat6'),
    true,
  );
  // And the cyclone axis alone.
  assert.equal(
    isAdvisoryStale(generatedForRemalObserved, 6, 'sagar', '1970324N05143', 'observed'),
    true,
  );
});

test('an SMS at the limit is not over', () => {
  const status = smsLengthStatus('x'.repeat(SMS_LIMIT));
  assert.equal(status.length, SMS_LIMIT);
  assert.equal(status.over, false);
  assert.equal(status.label, '160/160');
});

test('an SMS one character over is flagged', () => {
  const status = smsLengthStatus('x'.repeat(SMS_LIMIT + 1));
  assert.equal(status.over, true);
  assert.equal(status.label, '161/160');
});

test('length counts code points, not UTF-16 units', () => {
  // An astral character (one code point, two UTF-16 units) must count once.
  // Counting units would report an emoji as 2 and could flag a 159-char draft
  // as over the limit.
  const status = smsLengthStatus('🌊'.repeat(SMS_LIMIT));
  assert.equal(status.length, SMS_LIMIT);
  assert.equal(status.over, false);
});

test('an empty draft is 0/160 and not over', () => {
  const status = smsLengthStatus('');
  assert.equal(status.length, 0);
  assert.equal(status.over, false);
  assert.equal(status.label, '0/160');
});

test('only an explicit false says the shelters are verified', () => {
  assert.equal(sheltersAreDemoData({ is_demo_data: false }), false);
  assert.equal(sheltersAreDemoData({ is_demo_data: true }), true);
});

test('a missing or malformed shelter_status discloses rather than hides', () => {
  // Fails closed. A dropped /allocation must not silently remove a
  // "do not use this to direct evacuations" warning.
  for (const status of [null, undefined, {}, { is_demo_data: 'false' }, { is_demo_data: 0 }]) {
    assert.equal(sheltersAreDemoData(status), true, `${JSON.stringify(status)} should disclose`);
  }
});

// ---------------------------------------------------------------------------
// The cached-example fallback.
//
// Both branches of the offer are covered, because the negative one is the
// normal state: `mobile/sampleAdvisory.ts` exports `null` on a fresh checkout
// and stays null until the capture tool has made a successful live call.
// A button that renders unconditionally would leave a demo operator pressing
// something that does nothing.
// ---------------------------------------------------------------------------

test('with a capture bundled, the fallback is offered', () => {
  const offer = cachedSampleOffer({ captured_at: 'T', cached: true, response: {} });
  assert.equal(offer.available, true);
  assert.equal(offer.label, 'Load cached example');
});

test('with no capture, the fallback is not offered', () => {
  // The button must not exist, not exist-and-fail. `null` is what
  // `sampleAdvisory.ts` exports until the tool has run.
  assert.equal(cachedSampleOffer(null).available, false);
  assert.equal(cachedSampleOffer(undefined).available, false);
});

test('the offer never varies its copy, only its availability', () => {
  // A reader who sees the button twice should not see two different
  // explanations of what it does.
  const withSample = cachedSampleOffer({ captured_at: 'T' });
  const without = cachedSampleOffer(null);
  assert.equal(withSample.label, without.label);
  assert.equal(withSample.note, without.note);
  assert.ok(withSample.note.length > 0);
});

test('the banner names when it was captured and that it is not live', () => {
  // Both halves, always. "Cached" alone invites the reading that it is just
  // a fast path to the same fresh answer.
  const text = cachedBannerText('2026-09-29T10:00:00+00:00');
  assert.match(text, /2026-09-29T10:00:00\+00:00/);
  assert.match(text, /not live/i);
});

test('the banner is not dismissable phrasing — no "hide" or "dismiss"', () => {
  // Weakly enforced (it is a string), but it catches the specific regression
  // of adding a close affordance to the banner's copy.
  assert.doesNotMatch(cachedBannerText('T'), /dismiss|hide|close/i);
});
