"""Wind speed -> storm surge height, via the trained regression in data/surge_model.pkl.

The model is `data/surge_model.pkl`, a joblib dict
``{model, features, loo_mae}`` trained by
`backend/data_pipeline/train_surge_model.py` on 4 verified historical Bay of
Bengal cyclones. It is a genuine fitted LinearRegression, not a lookup table
(Rules.md).

Two honest caveats, both surfaced in every result rather than buried here:

1. **The fit is weak.** n=4 with 3 features, so the full fit interpolates
   exactly and LOOCV MAE is 2.36 m against a 0.6-2.9 m range. The fitted
   coefficients produce physically impossible output across the slider range
   (negative surge at low wind; +6.5 m at 250 kmph). The result therefore
   clamps to [0, SURGE_MAX_M] and reports `clamped` so no consumer can
   present the number as an observation.
2. **Two of three features are not observed.** The app's slider supplies wind
   speed only. Forward speed and approach angle are filled from documented
   IMD-category-typical values — ASSUMPTIONS, marked as such, and exposed in
   the result so the API can label the output an estimate.

The 1.2 m Remal anchor (CLAUDE.md) remains the reference figure in all
narrative copy; the regression is what drives the map.
"""

from __future__ import annotations

import joblib
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = REPO_ROOT / "data" / "surge_model.pkl"

SURGE_MIN_M = 0.0
SURGE_MAX_M = 4.0  # Bay of Bengal landfall surge above astronomical tide; see CLAUDE.md

# IMD wind classification, 3-min mean sustained wind in km/h.
# Source: India Meteorological Department, cyclone wind classification table.
# Ordered high-to-low at evaluation time; the slider is 31-250 kmph.
IMD_CATEGORIES: tuple[tuple[float, str], ...] = (
    (120, "Super Cyclonic Storm"),
    (90, "Extremely Severe Cyclonic Storm"),
    (64, "Very Severe Cyclonic Storm"),
    (48, "Severe Cyclonic Storm"),
    (34, "Cyclonic Storm"),
    (28, "Deep Depression"),
    (17, "Depression"),
)

# ASSUMPTION (not measured): forward speed and approach-angle flag typical of
# Bay of Bengal landfalling systems, used when the caller does not supply
# them. Remal itself moved at roughly 15-16 km/h and made an approximately
# head-on landfall (flag 1), which is the 115/16/1 row of the training table.
TYPICAL_FORWARD_SPEED_KMPH = 15.0
TYPICAL_APPROACH_ANGLE_FLAG = 1  # 1 = head-on/orthogonal, 0 = oblique


@dataclass(frozen=True)
class SurgeResult:
    """Surge prediction plus everything a caller needs to caveat it."""

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


def imd_category(wind_kmph: float) -> str:
    """IMD classification label for a 3-min mean wind in km/h."""
    for threshold, label in IMD_CATEGORIES:
        if wind_kmph >= threshold:
            return label
    return "Depression"


@lru_cache(maxsize=1)
def _load_model() -> dict:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Surge model not found at {MODEL_PATH}. Pre-fetched and committed by "
            "design (Rules.md); retrain with "
            "`venv/bin/python backend/data_pipeline/train_surge_model.py`."
        )
    return joblib.load(MODEL_PATH)


def predict_surge(
    wind_kmph: float,
    forward_speed_kmph: float | None = None,
    approach_angle_flag: int | None = None,
) -> SurgeResult:
    """Predict surge height for a storm.

    Only `wind_kmph` is required — the app's slider supplies it. The other two
    model features default to documented typical values and are flagged as
    assumed in the result.
    """
    bundle = _load_model()
    model = bundle["model"]
    loo_mae = float(bundle["loo_mae"])

    forward_assumed = forward_speed_kmph is None
    forward = TYPICAL_FORWARD_SPEED_KMPH if forward_assumed else float(forward_speed_kmph)
    approach_assumed = approach_angle_flag is None
    approach = TYPICAL_APPROACH_ANGLE_FLAG if approach_assumed else int(approach_angle_flag)

    features = [[float(wind_kmph), forward, float(approach)]]
    raw = float(model.predict(features)[0])

    surge = min(max(raw, SURGE_MIN_M), SURGE_MAX_M)
    return SurgeResult(
        wind_kmph=float(wind_kmph),
        imd_category=imd_category(wind_kmph),
        surge_m=surge,
        raw_prediction_m=raw,
        clamped=surge != raw,
        loo_mae_m=loo_mae,
        # Always an estimate: even a caller that supplies all three features is
        # extrapolating a 4-point fit. The label reflects model quality, not
        # just which fields were defaulted.
        is_estimate=True,
        forward_speed_kmph=forward,
        approach_angle_flag=approach,
        forward_speed_assumed=forward_assumed,
        approach_angle_assumed=approach_assumed,
    )
