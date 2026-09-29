/**
 * Advisory presentation logic — everything about *how a failure reads* and
 * *whether a result still matches the screen*, kept out of the component so it
 * can be tested without a renderer.
 *
 * This module imports nothing from `react-native` (it imports only types from
 * `api.ts`, which itself imports nothing native). That is the same property
 * `api.ts` has and the same reason `node --test` works on it directly — see
 * `tests/advisoryFlow.test.mjs`.
 *
 * Two rules are load-bearing here and both were decided rather than derived:
 *
 *   1. **Branch on `ApiErrorKind`, never on a status code.** The same 502
 *      means "the advisory was withheld for inventing a locality" and "the
 *      model died", and the two deserve different words. A status is not a
 *      reason.
 *   2. **A failure that will not clear gets no retry button.** Quota resets at
 *      midnight Pacific; offering "Try again" there invites a user to burn
 *      time and the rest of the day's quota against a limit that has already
 *      run out. The `AdvisoryFailureAction` union makes that structural — the
 *      quota branch cannot return a retry action even by accident.
 */
import type { AdvisoryResponse, ApiError } from './api';

/**
 * What the failure state offers the user.
 *
 * A union rather than a boolean `canRetry` so the distinctions survive into
 * the UI: `wait` is a real action (an instruction, not a button), and
 * `reload` is a different remedy from `retry` — a config error does not
 * improve by asking the server again.
 *
 * Every variant carries a `label`, so a failure state can never render with
 * no action and no explanation. There is deliberately no "no action" variant:
 * a failure with nothing to say and nothing to offer is a dead end, and every
 * branch below can say at least one useful sentence.
 */
export type AdvisoryFailureAction =
  /** A button that spends quota again. */
  | { readonly type: 'retry'; readonly label: string }
  /** Not a button — prose telling the user when, or what, to do next. */
  | { readonly type: 'wait'; readonly label: string }
  /** A build-time problem: the URL is compiled in, so only a reload fixes it. */
  | { readonly type: 'reload'; readonly label: string };

export interface AdvisoryFailure {
  readonly title: string;
  readonly body: string;
  readonly action: AdvisoryFailureAction;
  /** Honesty-check violations, when the failure was `kind: 'validation'`. */
  readonly violations: readonly string[];
}

/**
 * The message for an advisory failure, keyed on the error's `kind`.
 *
 * Every branch is reachable: the backend and `api.ts` between them produce all
 * seven. The one that was wrong before this existed was `quota`, which used to
 * arrive as a 502 and therefore read as `upstream` — "something failed" — to a
 * user whose actual problem was a spent daily limit.
 *
 * The `switch` is exhaustive over `ApiErrorKind` with no `default`, so adding
 * a ninth kind to `api.ts` is a compile error here rather than a branch that
 * silently falls through to the wrong message.
 */
export function describeAdvisoryError(error: ApiError): AdvisoryFailure {
  switch (error.kind) {
    case 'config':
      return {
        title: 'Backend not configured',
        body:
          'The API address is compiled into this build, so the app has nowhere ' +
          'to send the request. This is fixed at build time, not at runtime — ' +
          'nothing in the app can repair it.',
        action: {
          type: 'reload',
          label: 'Set EXPO_PUBLIC_API_URL, restart with -c, then reload.',
        },
        violations: [],
      };

    case 'timeout':
      return {
        title: 'The advisory took too long',
        // The one failure where the useful sentence is the uncomfortable one.
        body:
          'The request was given 120 seconds and the model did not answer in ' +
          'time. It may still be running on the server, and the call has ' +
          'already spent Gemini quota either way — so sending the same ' +
          'request again immediately can double-spend a scarce daily limit ' +
          'for nothing.',
        action: {
          type: 'wait',
          label: 'Wait a few minutes, then reload if you still need it.',
        },
        violations: [],
      };

    case 'network':
      return {
        // Deliberately no blame in either direction: not "the app is broken",
        // not "the server is down". The phone may have left Wi-Fi mid-request.
        title: "Couldn't reach the server",
        body:
          'The request never got an answer. This says nothing about whether ' +
          'the simulation or the model worked — the connection failed before ' +
          'either was reached.',
        action: { type: 'retry', label: 'Try again' },
        violations: [],
      };

    case 'capacity':
      return {
        title: 'The advisory model is at capacity',
        body:
          'Gemini returned 503 UNAVAILABLE on every attempt, with retries ' +
          'and backoff in between. This is a busy model, not a fault in the ' +
          'simulation, and the same request is worth sending again shortly. ' +
          (error.retryAfterSeconds === null
            ? ''
            : `The server asked for a wait of about ${error.retryAfterSeconds}s.`),
        action: { type: 'retry', label: 'Try again' },
        violations: [],
      };

    case 'quota':
      return {
        title: "Today's request limit is used up",
        body:
          "Gemini's free-tier daily request limit has been reached, so " +
          'advisories are unavailable until it resets at midnight Pacific. ' +
          'Retrying will not help and each attempt spends what is left of ' +
          'the day. This is a usage limit, not a fault: every other part of ' +
          'the app is unaffected and still working, and any advisory already ' +
          'generated is still readable.',
        // No retry, and no countdown. A countdown here would be the most
        // actively harmful thing the screen could show: it would promise
        // that waiting helps, and poll until the user's own midnight.
        action: {
          type: 'wait',
          label: 'The limit resets at midnight Pacific.',
        },
        violations: [],
      };

    case 'validation':
      return {
        title: 'Withheld — it failed its honesty checks',
        body:
          'The model drafted an advisory that named something the simulation ' +
          'did not produce, so the backend withheld it rather than show it. ' +
          'Nothing was returned to display.',
        action: { type: 'retry', label: 'Draft it again' },
        // The point of this branch: the user is told which checks failed, not
        // just that something did.
        violations: error.violations,
      };

    case 'upstream':
      return {
        title: 'The advisory could not be generated',
        body:
          'The call to the model failed in a way the backend does not ' +
          'classify. The simulation behind it is unaffected.',
        action: { type: 'retry', label: 'Try again' },
        violations: [],
      };

    case 'http':
      return {
        // `status` is a number here, never 0: `http` means a response arrived
        // and its code was not one of the classified ones.
        title: `Unexpected response (${error.status})`,
        body:
          'The server answered with something this app does not have a ' +
          'message for. The detail, if any, is below.',
        action: { type: 'retry', label: 'Try again' },
        violations: [],
      };
  }
}

/**
 * The `${category}:${origin}` key the stale guard compares.
 *
 * Both halves are required. Category alone misses an advisory that is correct
 * for the intensity but written for the wrong town; origin alone misses one
 * that is right for the town and wrong for the storm.
 */
export function advisoryKey(category: number, origin: string): string {
  return `${category}:${origin}`;
}

/**
 * Is a returned advisory now describing a scenario the screen has moved off?
 *
 * Compared against the **server's** echo in `generated_for`, not against the
 * arguments the client sent. The server's copy is what the prose was actually
 * written for, so it is the only one of the two that can be trusted to
 * describe the advisory in hand.
 */
export function isAdvisoryStale(
  response: AdvisoryResponse,
  category: number,
  origin: string,
): boolean {
  const generated = response.generated_for;
  return (
    advisoryKey(generated.category, generated.origin.id) !== advisoryKey(category, origin)
  );
}

/** The SMS draft's limit. Mirrors the Gemini schema's field description. */
export const SMS_LIMIT = 160;

/**
 * Are the shelters behind this advisory unverified placeholder data?
 *
 * Read from `shelter_status.is_demo_data` on `/allocation` and **nowhere
 * else**. The model writes fluent, confident prose and will not volunteer
 * that the shelters it is allocating people to do not exist, so a notice
 * sourced from the advisory's own text would be sourced from the one
 * component that has every reason to hide the fact. The flag comes from the
 * simulation layer, which is where the truth is.
 *
 * **Fails closed.** An absent, null or malformed block returns `true`, so a
 * failed `/allocation` shows the disclosure rather than suppressing it. The
 * reverse default would let a transient network error silently drop a
 * "do not use this to direct evacuations" warning, which is the single worst
 * failure this app could have.
 */
export function sheltersAreDemoData(status: Record<string, unknown> | null | undefined): boolean {
  if (status === null || status === undefined) return true;
  const flag = status['is_demo_data'];
  // Only an explicit `false` counts as verified. `true`, a missing key, a
  // string, a number — all disclose.
  return flag !== false;
}

export interface SmsLengthStatus {
  readonly length: number;
  readonly limit: number;
  /** Over the limit. The copy button stays enabled — see the note below. */
  readonly over: boolean;
  /** `"143/160"`. */
  readonly label: string;
}

/**
 * Live length for the SMS draft.
 *
 * Counted in code points (`Array.from`) rather than UTF-16 units, so an
 * astral character counts once instead of twice. This is deliberately not a
 * strict GSM-7 count: a draft containing any character outside that charset is
 * sent as UCS-2, where the real limit is 70, not 160. A full GSM-7 table
 * would catch that; the honest thing today is the simpler count plus this
 * note, since the model writes plain ASCII for this audience in practice.
 *
 * Over-limit is reported, not enforced. The draft is Gemini's text and the
 * user may well want to send it anyway, so the copy button is not disabled —
 * a hidden button on a 165-character draft is a worse failure than a visible
 * over-limit count.
 */
export function smsLengthStatus(draft: string): SmsLengthStatus {
  const length = Array.from(draft).length;
  return {
    length,
    limit: SMS_LIMIT,
    over: length > SMS_LIMIT,
    label: `${length}/${SMS_LIMIT}`,
  };
}
