"""Train the storm peak intensity model, and report whether it earned anything.

    python -m backend.data_pipeline.train_storm_peak_intensity
    python -m backend.data_pipeline.train_storm_peak_intensity --out data/ml/storm_peak_intensity.json

The model is Ridge on standardised features, cross-validated leave-one-out over
every North Indian Ocean storm in the archive at or after 1970 that has a
measured `USA_WIND`. It predicts a storm's **peak sustained wind over its
lifetime**, in knots — see `backend/ml/storm_peak_intensity.py` for why it is
not called "landfall intensity".

**This script exits 0 whether or not the model beats the baseline, because
losing is a real and reportable result.** The gate is recorded in the artefact
and travels to the client; `estimate_for()` then ships the flat median and
refuses to call the number a prediction. If this exited non-zero on a failed
gate, the honest outcome would read as a broken build, and the tempting fix
would be to tune until it passed. `--require-gate` exists for the opposite
case — CI that should break if a regression makes the model worse — and is off
by default so that the default path records rather than blocks.

**The bar is a flat median, and the model does not clear it.** Leave-one-out
mean absolute error is around 21.9 kt against the baseline's 21.0 kt. That
result is the output, not a bug to be tuned away; the features available at
prediction time carry almost no signal about peak intensity in this basin.

A leakage guard runs before anything is written, not after: a feature that
correlates with the target above a fixed threshold aborts the run, because the
first version of this module scored 3.5 kt by including the running maximum
immediately before each storm's peak, which is the target arriving through a
side door. `--max-abs-correlation` is exposed so the threshold is a decision
someone can see rather than a constant buried in the trainer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ml.storm_peak_intensity import (  # noqa: E402
    FEATURE_NAMES,
    StormPeakIntensityModel,
    build_training_set,
    evaluate,
)

DEFAULT_OUT = REPO_ROOT / "data" / "ml" / "storm_peak_intensity.json"

#: No feature may correlate with the target more strongly than this. 0.9 is far
#: above anything a real meteorological relationship would reach here and far
#: below the 0.97 the known leak scored, so the guard has room to be strict.
DEFAULT_MAX_ABS_CORRELATION = 0.9


def find_leaks(rows, threshold: float) -> list[tuple[str, float]]:
    """Features whose correlation with the target is implausibly high.

    A correlation this strong does not mean a useful relationship, it means the
    feature was computed from the answer. Failing the run is right: an artefact
    built on a leak is worse than no artefact, because it looks excellent.
    """
    if not rows:
        return []
    X = np.array([r.features for r in rows], dtype=float)
    y = np.array([r.target_kt for r in rows], dtype=float)
    if y.std() == 0:
        return []
    leaks: list[tuple[str, float]] = []
    for index, name in enumerate(FEATURE_NAMES):
        column = X[:, index]
        if column.std() == 0:
            continue
        correlation = float(np.corrcoef(column, y)[0, 1])
        if abs(correlation) > threshold:
            leaks.append((name, correlation))
    return leaks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--since", type=int, default=1970)
    parser.add_argument(
        "--max-abs-correlation",
        type=float,
        default=DEFAULT_MAX_ABS_CORRELATION,
        help="abort if any feature correlates with the target above this",
    )
    parser.add_argument(
        "--require-gate",
        action="store_true",
        help="exit non-zero when the model loses to the flat median baseline",
    )
    args = parser.parse_args(argv)

    rows = build_training_set(since=args.since)
    if len(rows) < 3:
        print(f"only {len(rows)} usable storms; refusing to fit", file=sys.stderr)
        return 2

    print(f"training storms  {len(rows)}  (NI, season >= {args.since}, USA_WIND present)")
    print(f"features         {len(FEATURE_NAMES)}: {', '.join(FEATURE_NAMES)}")

    leaks = find_leaks(rows, args.max_abs_correlation)
    if leaks:
        print("\nLEAK DETECTED — not writing an artefact.", file=sys.stderr)
        for name, correlation in leaks:
            print(
                f"  {name} correlates with the target at {correlation:+.4f}, "
                f"above the {args.max_abs_correlation} limit",
                file=sys.stderr,
            )
        print(
            "\nA feature this strongly correlated with the target was computed "
            "from it. An artefact fitted on that looks excellent and predicts "
            "nothing. Drop the feature rather than raising the threshold.",
            file=sys.stderr,
        )
        return 3

    report = evaluate(rows)
    model = StormPeakIntensityModel().fit(rows)
    model.to_json(args.out)

    print(f"\nleave-one-out    {report.n} folds")
    print(f"  model MAE      {report.mae_kt:6.2f} kt")
    print(f"  baseline MAE   {report.baseline_mae_kt:6.2f} kt   (flat median)")
    print(f"  R2             {report.r2:6.3f}")
    print(f"  median target  {np.median([r.target_kt for r in rows]):6.1f} kt")
    print(f"\nwrote            {args.out}")

    if report.beats_baseline:
        print(f"GATE             PASSED ({report.mae_kt:.2f} < {report.baseline_mae_kt:.2f})")
        print("                 estimate_for() will show the model's figure.")
        return 0

    print(f"GATE             FAILED ({report.mae_kt:.2f} >= {report.baseline_mae_kt:.2f})")
    print(
        "                 The flat median is the honest figure and is what "
        "ships."
    )
    print(
        "                 This is recorded, not tuned away. The gate's purpose "
        "is to stop a"
    )
    print(
        "                 worse-than-constant model from being presented as a "
        "prediction."
    )
    return 1 if args.require_gate else 0


if __name__ == "__main__":
    raise SystemExit(main())