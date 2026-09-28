"""The retired 3-feature LinearRegression surge model, kept for the record.

NOT ON THE RUNTIME PATH. `backend/simulation/surge.py` no longer imports this,
and nothing in `backend/main.py` does either. It is here so the decision to
replace it (MEMORY.md "Flagged for review") can be re-examined against the
code that produced the numbers being replaced, rather than against a prose
summary.

What it was: `surge_m = f(wind_kmph, forward_speed_kmph, approach_angle_flag)`,
fitted by ordinary least squares on four historical Bay of Bengal cyclones
and loaded at runtime from `surge_model.pkl` (a joblib dict
`{model, features, loo_mae}`).

Its four measured weaknesses, none of which are fixable by adding more rows
alone:

  * **Exactly determined.** 4 training rows, 3 feature columns, 4 fitted
    parameters (3 coefficients + intercept) => **zero residual degrees of
    freedom**. The full fit interpolates all four training points to ~1e-16.
    A model that cannot be wrong in-sample has no redundancy to spend on
    generalisation, which is why the leave-one-out error is so much larger
    than the fit suggests.

  * **LOOCV MAE 2.36 m** against a training range of 0.6-2.9 m — an error bar
    wider than the quantity being predicted. Per-point, it is worse than the
    mean suggests:

        Remal 2024   actual 1.2 m -> LOO 2.679 m  (err 1.479, 123% of actual)
        Helen 2013   actual 1.6 m -> LOO 0.019 m  (err 1.581,  99% of actual)
        Lehar 2013   actual 2.9 m -> LOO -0.026 m (err 2.926, 101% of actual)
        Mandous 2021 actual 0.6 m -> LOO 4.053 m  (err 3.453, 575% of actual)

    Every point is wrong by roughly its own magnitude, and the two largest
    predictions are physically absurd (negative surge; 4 m from a 0.6 m
    event). A **mean-baseline** predictor, scored under the same
    leave-one-out splits, returns the mean of the training folds for every
    input and achieves an MAE of **0.90 m** — **2.6x better** than the
    regression's 2.36 m. The fitted model is beaten by ignoring its inputs.

  * **3 of 4 rows unsourced.** `train_surge_model.py` attributes all four to
    "the four verified real historical cases from CLAUDE.md". CLAUDE.md
    contains no such table — it names Remal only. Helen (1.6 m), Lehar (2.9 m)
    and Mandous (0.6 m) are asserted without a traceable citation anywhere in
    the repo, and Lehar's 2.9 m is what forces the extrapolation to 5 m+.

  * **Two of three features were never observed.** The app's slider supplies
    wind speed only; forward speed and approach angle were filled with
    documented-typical constants (15 km/h, head-on), so the model spent two
    of its three degrees of freedom on assumed inputs.

What replaced it: a single-anchor quadratic scaling law in
`backend/simulation/surge.py` — `surge_m = 1.2 * (wind_kmph / 115) ** 2` —
which is monotone, exactly reproduces the one observed event it is anchored
to, needs no fitting, and has no degrees of freedom left to overfit. It is
less expressive than the regression and deliberately so: with one observation,
complexity is not recoverable.

To re-run the retired model for comparison:

    venv/bin/python -m backend.experiments.surge_regression.train_surge_model

which rewrites `surge_model.pkl` in this directory and prints the LOOCV
per-point table above.
"""

from __future__ import annotations

import joblib
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent / "surge_model.pkl"

SURGE_MIN_M = 0.0
SURGE_MAX_M = 4.0

# The two assumed features, retained so the retired model is reproducible.
TYPICAL_FORWARD_SPEED_KMPH = 15.0
TYPICAL_APPROACH_ANGLE_FLAG = 1


@dataclass(frozen=True)
class RetiredSurgeResult:
    """The old result shape, preserved verbatim for comparison."""

    wind_kmph: float
    imd_category: str
    surge_m: float
    raw_prediction_m: float
    clamped: bool
    loo_mae_m: float
    is_estimate: bool
    forward_speed_kmph: float
    approach_angle_flag: int
    forward_speed_assumed: bool
    approach_angle_assumed: bool

    def to_dict(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=1)
def load_model() -> dict:
    return joblib.load(MODEL_PATH)


def predict_surge_retired(
    wind_kmph: float,
    forward_speed_kmph: float | None = None,
    approach_angle_flag: int | None = None,
) -> RetiredSurgeResult:
    """The retired model, callable for side-by-side comparison only."""
    from backend.simulation.surge import imd_category

    bundle = load_model()
    model = bundle["model"]
    loo_mae = float(bundle["loo_mae"])

    forward_assumed = forward_speed_kmph is None
    forward = TYPICAL_FORWARD_SPEED_KMPH if forward_assumed else float(forward_speed_kmph)
    approach_assumed = approach_angle_flag is None
    approach = TYPICAL_APPROACH_ANGLE_FLAG if approach_assumed else int(approach_angle_flag)

    raw = float(model.predict([[float(wind_kmph), forward, float(approach)]])[0])
    surge = min(max(raw, SURGE_MIN_M), SURGE_MAX_M)
    return RetiredSurgeResult(
        wind_kmph=float(wind_kmph),
        imd_category=imd_category(wind_kmph),
        surge_m=surge,
        raw_prediction_m=raw,
        clamped=surge != raw,
        loo_mae_m=loo_mae,
        is_estimate=True,
        forward_speed_kmph=forward,
        approach_angle_flag=approach,
        forward_speed_assumed=forward_assumed,
        approach_angle_assumed=approach_assumed,
    )
