"""Capture ONE real advisory to disk, as a demo fallback.

    RUN_LIVE_CAPTURE=1 venv/bin/python -m backend.tools.capture_advisory

Outputs
-------
data/samples/advisory_cat<N>_sagar.json   the raw response, wrapped
mobile/sampleAdvisory.ts                  the same bytes, as a bundled module

Why this exists
---------------
The advisory is the one screen that needs a network round trip, a model that
can be at capacity, and a daily quota that a hackathon demo can exhaust in
three runs. Every other endpoint in this app is local and deterministic. So
when the model is unavailable at the moment someone opens the app, the
fallback has to be **a real captured advisory** — the same JSON the endpoint
returned, byte for byte.

Which is the whole constraint: **this file does not invent advisory content.**
There is no hand-written sample, no template, no "example" advisory assembled
from strings in a test. If no live call succeeds, nothing is written and the
app ships with no fallback at all, which is the correct outcome — an invented
advisory presented as model output is the one failure this project cannot
have. A missing sample is visible and harmless; a fabricated one is neither.

How it is gated
---------------
Opt-in via `RUN_LIVE_CAPTURE=1`, matching the `RUN_LIVE_TESTS=1` convention
already in `tests/test_module_d.py`. Without it this exits before importing
anything that could make a request. A populated `.env` is *not* sufficient
opt-in, for the same reason the live tests work that way: the free tier
allows 20 calls/day/project/model, so "a key happens to be present" must
never be what spends the quota.

**Exactly one POST /advisory is attempted. There is no retry loop.** That is
deliberate and it cuts against the backend's own capacity ladder, which will
happily retry a 503 three times with backoff. For a *capture* that is the
wrong behaviour: if the model is busy, the honest result is "not captured",
and the operator can try again later on purpose. An automatic retry here
would spend up to six calls to produce a file nobody asked for twice.

`/allocation` is queried to pick a category, but that is a local GET — it
touches no model and spends no quota.

Schema note
-----------
The brief for this file asked to gate on `validation.passed`. **There is no
such field.** The endpoint returns a top-level `validated: true`, hardcoded,
and the real validation detail lives in `validation.{checks,plan_coverage,
attempts,gemini_calls}`. A 200 therefore *always* has `validated: true` — the
backend withholds a failing advisory with a 502 rather than returning one
flagged false. The gate below checks `validated is True` because that is the
field that exists, and a capture on a 200 is what actually needs checking:
the 502 path is the one that withholds. Logged in MEMORY.md "Flagged for
review"; CLAUDE.md's reference schema is untouched.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import main  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "data" / "samples"
ORIGIN = "sagar"

#: Tried first. Category 5 is the floor at which the app exposes any
#: infrastructure at all (0 hospitals, 0 substations, 0 roads at 0-4), so it is
#: the smallest intensity whose advisory has anything to advise about. If it
#: somehow has an empty allocation, `resolve_category` walks down.
PREFERRED_CATEGORY = 5

#: The mobile module. Generated rather than imported, for a reason that is a
#: real constraint and not a preference: there is no `metro.config.js` in
#: `mobile/`, so Metro resolves imports from the project root only and cannot
#: reach `data/` at all. A file outside `mobile/` is unbundleable. And the
#: filename embeds the category, which is not known until the capture happens,
#: so a static `import` of the JSON cannot have a fixed path either.
#:
#: Generating a module solves both: the import path is stable whatever N is,
#: and the "does a sample exist?" question becomes `SAMPLE_ADVISORY === null`
#: rather than a filesystem check the app cannot do synchronously.
MOBILE_MODULE = REPO_ROOT / "mobile" / "sampleAdvisory.ts"

CAPTURE_ENV = "RUN_LIVE_CAPTURE"


class CaptureRefused(RuntimeError):
    """The opt-in guard did not open, so nothing was attempted."""


# ---------------------------------------------------------------------------
# Pure helpers — no I/O, no model. These are what the tests exercise.
# ---------------------------------------------------------------------------


def build_sample(payload: dict, captured_at: str) -> dict:
    """Wrap a raw `/advisory` response in its capture envelope.

    `cached: true` is in the envelope rather than inferred by the client, so a
    reader of the file on disk can tell a capture from a live response without
    knowing anything about this script. `captured_at` is passed in rather than
    read from the clock so the result is a pure function of its arguments.
    """
    return {
        "captured_at": captured_at,
        "cached": True,
        "response": payload,
    }


def is_capturable(payload: dict) -> tuple[bool, str]:
    """Should this 200 body be written to disk? Returns `(ok, reason)`.

    Checks `validated is True` — the field that exists — rather than the
    `validation.passed` the brief named, which does not. `is True` rather than
    truthiness so a string, a number, or a missing key all fail closed.
    """
    if not isinstance(payload, dict):
        return False, f"expected a JSON object, got {type(payload).__name__}"
    if "advisory" not in payload:
        return False, "response has no 'advisory' key — not an advisory response"
    if payload.get("validated") is not True:
        return (
            False,
            f"response is not validated: `validated` is {payload.get('validated')!r}, "
            "expected True",
        )
    return True, "ok"


def resolve_category(allocation_counts: dict[int, int], preferred: int = PREFERRED_CATEGORY) -> int:
    """Pick the category to capture at.

    `allocation_counts` maps category -> number of allocation rows. The brief:
    category 5, or the highest category with a non-empty allocation. So the
    preferred category wins if it qualifies, and otherwise the highest that
    does is taken.

    An advisory at a category with nothing allocated would be the model
    correctly saying "there is nothing to evacuate" — true, and useless as a
    demo fallback, because the interesting part is the evacuation plan.
    """
    if allocation_counts.get(preferred, 0) > 0:
        return preferred
    for category in sorted(allocation_counts, reverse=True):
        if allocation_counts[category] > 0:
            return category
    raise CaptureRefused(
        "no category has a non-empty allocation — nothing to capture an advisory about"
    )


def render_mobile_module(sample: dict | None) -> str:
    """The TypeScript that `mobile/` imports.

    `None` yields a module exporting `null`, which is what the app checks to
    decide whether to offer the fallback at all. That is the mechanism behind
    the "button must not render when the file is missing" rule: there is no
    filesystem check in the app to get wrong, only a null comparison.

    The body is `json.dumps` output, which is a valid JS/TS expression, so the
    bundled bytes are the captured bytes rather than a re-serialisation with
    lossy number formatting.
    """
    header = (
        "/**\n"
        " * GENERATED FILE — do not edit.\n"
        " *\n"
        " * Written by `backend/tools/capture_advisory.py` from a single live\n"
        " * `POST /advisory`. Re-run that tool to replace it; hand-editing this\n"
        " * would put content in the app that no model ever produced, which is\n"
        " * the one thing this project must not do.\n"
        " *\n"
        " * `null` means no capture has succeeded yet, and the app offers no\n"
        " * cached fallback. That is a supported state, not a broken build.\n"
        " */\n"
        "import type { CachedAdvisory } from './api';\n\n"
    )
    if sample is None:
        return header + "export const SAMPLE_ADVISORY: CachedAdvisory | null = null;\n"

    body = json.dumps(sample, indent=2, ensure_ascii=False)
    return f"{header}export const SAMPLE_ADVISORY: CachedAdvisory | null = {body};\n"


def sample_path(category: int) -> Path:
    return SAMPLE_DIR / f"advisory_cat{category}_{ORIGIN}.json"


# ---------------------------------------------------------------------------
# The live shell.
# ---------------------------------------------------------------------------


def _require_opt_in() -> None:
    if os.environ.get(CAPTURE_ENV) != "1":
        raise CaptureRefused(
            f"{CAPTURE_ENV} is not set to 1. This makes a real Gemini call and "
            "spends daily quota, so it never runs by accident — a populated "
            ".env is deliberately not enough. Re-run with:\n"
            f"    {CAPTURE_ENV}=1 venv/bin/python -m backend.tools.capture_advisory"
        )


def capture(client, category: int = PREFERRED_CATEGORY, origin: str = ORIGIN) -> dict:
    """Make the one POST attempt and return `(status_code, body)`.

    Takes the client as an argument so tests can pass a fake and never reach
    a model. Deliberately a single call: no loop, no backoff, no second try at
    a different category. A failed capture is a fact to report, not a
    condition to retry through.
    """
    response = client.post(f"/advisory?category={category}&origin={origin}")
    try:
        body = response.json()
    except ValueError:
        # A non-JSON body means the app behind the app is broken (a proxy
        # error page, say). Report the status and the text, save nothing.
        return response.status_code, {"_raw": response.text[:500]}
    return response.status_code, body


def _allocation_counts(client) -> dict[int, int]:
    """Allocation row count per category. Local, deterministic, no quota."""
    counts: dict[int, int] = {}
    for category in range(7):
        payload = client.get(f"/allocation?category={category}&origin={ORIGIN}").json()
        counts[category] = len(payload.get("allocation", {}).get("assignments", []))
    return counts


def main_entry() -> int:
    from fastapi.testclient import TestClient

    _require_opt_in()

    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set — POST /advisory would return 428.")
        print("Nothing captured. Set the key in the environment (never in a tracked file).")
        return 1

    client = TestClient(main.app)

    counts = _allocation_counts(client)
    try:
        category = resolve_category(counts)
    except CaptureRefused as exc:
        print(f"Cannot pick a category: {exc}")
        return 1

    print(
        f"allocation rows per category: "
        + ", ".join(f"{c}={counts[c]}" for c in sorted(counts))
    )
    print(f"capturing category={category} origin={ORIGIN} — one attempt, no retries")

    status, body = capture(client, category)

    if status != 200:
        print(f"\nHTTP {status} — nothing captured, nothing written.")
        detail = body.get("detail") if isinstance(body, dict) else body
        if isinstance(detail, str):
            print(f"detail: {detail}")
        elif isinstance(detail, dict):
            # A 502-with-violations is the honesty check withholding a draft.
            # Naming the violations is the useful part of a failed capture.
            print(f"detail: {detail.get('message', detail)}")
            for violation in detail.get("violations", []) or []:
                print(f"  - {violation}")
        return 1

    ok, reason = is_capturable(body)
    if not ok:
        print(f"\nHTTP 200 but not capturable: {reason} — nothing written.")
        return 1

    sample = build_sample(body, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    path = sample_path(category)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample, indent=2, ensure_ascii=False) + "\n")
    MOBILE_MODULE.write_text(render_mobile_module(sample))

    calls = body.get("validation", {}).get("gemini_calls")
    print(f"\ncaptured -> {path.relative_to(REPO_ROOT)}")
    print(f"bundled -> {MOBILE_MODULE.relative_to(REPO_ROOT)}")
    print(f"model: {body.get('model')}  gemini_calls: {calls}  captured_at: {sample['captured_at']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main_entry())
    except CaptureRefused as exc:
        print(f"refused: {exc}")
        sys.exit(2)
