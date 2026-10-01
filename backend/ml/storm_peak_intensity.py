"""ML storm peak intensity — and the honest possibility that it does not work.

## What this predicts, and why it is not called "landfall intensity"

The target is **the peak sustained wind a storm attained over its whole
lifetime**, from IBTrACS `USA_WIND`.

It is **not** an intensity at a landfall. Naming it that would misdescribe the
number, which is the same class of error as the "trained regression" wording
this project corrected in 2026-10-01: a label that flatters a method it is not.

A genuine landfall target would need a **landfall window** — the set of fixes
around the moment of landfall — and IBTrACS does not provide one that can be
defined rigorously from the fields present:

- `LANDFALL` is **not a boolean, though it is built to look like one.** The
  archive's own units row gives the column as **kilometres**, and every value is
  a whole number — so the 22,007 rows reading `0` are indistinguishable from a
  flag by eye. They are not: 33,988 North Indian Ocean rows carry a positive
  distance, reaching 1,463 km, and 1,857 are blank. Reading the column as a
  boolean would stamp "at the coast" onto 38% of the rows and throw away the
  distance the rest of the column was carrying.
- `DIST2LAND` alone cannot say *which* crossing is the landfall of interest. A
  Bay of Bengal storm typically crosses land several times, and Sagar Island is
  not necessarily the first. Picking one needs a coastline-distance join or an
  agency landfall time, neither of which is in this dataset.

So: peak intensity it is, honestly named. A landfall-window target is real future
work with its own validation, not something to approximate from a flag.

## The gate, which is the point of this module

**n = 300.** Measured, not guessed: North Indian Ocean storms at season ≥ 1970
whose `USA_WIND` is non-negative anywhere in the file.

That is small enough that a flat median is a serious competitor, so
`evaluate()` reports the median baseline's MAE alongside the model's and sets
`beats_baseline`. If the model does not beat it, **the baseline is what ships**,
labelled as such, and `beats_baseline` travels to the client so the UI can refuse
to call the number a prediction.

**Do not tune the model until the gate passes.** A failed gate reported honestly
is the correct outcome for n = 300 and is worth more than a number that only
looks good because the features were chosen after seeing the errors.

## This is not the surge model

`backend/simulation/surge.py` computes `1.2 * (wind/115)**2` and is
authoritative for every surge figure the app ships. Nothing in this module
reaches it, and an AST-level test in `tests/test_cache_isolation.py` enforces
that. A model estimate and a deterministic law are shown as two separate
figures, always labelled as different things.

## Units

IBTrACS `USA_WIND` is a **1-minute mean sustained wind in knots**. The IMD bands
the rest of the app uses are **3-minute means**. They are different quantities
and this module never converts between them silently — the target stays in knots
and says so.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import LeaveOneOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backend.cyclones.historical import DEFAULT_IBTRACS_PATH, ibtracs_number, read_ni_rows

#: The study area's centre, for the "how far from here" feature. Same point the
#: map opens on, so the feature means what a reader would expect.
STUDY_CENTRE = (21.95, 88.05)

#: Knots to km/h. The same constant the ATCF parser uses, so a live fix and a
#: historical fix are on one scale.
KNOTS_TO_KMPH = 1.852

#: Nautical miles to km, for `DIST2LAND`.
NM_TO_KM = 1.852

#: Feature order is part of the artefact. Changing it invalidates every
#: previously written `data/ml/storm_peak_intensity.json`.
#: **`peak_before_kmph` was here and is deliberately not any more.**
#:
#: It was the running maximum immediately *before* each storm's peak-wind fix —
#: within 5 kt of the target for 89% of the training storms and correlated with
#: it at 0.97. A model carrying it scored a leave-one-out MAE of 3.5 kt against
#: a flat median's 21.0, which looked like a very good model. It was not a model
#: of anything: **the feature is the target arriving through a side door**, and it
#: is not available at prediction time anyway, since predicting a storm's peak
#: wind is what you cannot already know.
#:
#: Dropping it moved the leave-one-out MAE to 21.9 kt, which does not beat the
#: flat median. That is the honest number, and `evaluate()` now reports it.
#: Re-adding the feature would restore the 3.5 kt figure and mean nothing.
FEATURE_NAMES: tuple[str, ...] = (
    "translation_speed_kmph",
    "bearing_deg",
    "distance_to_land_km",
    "latitude",
    "longitude",
    "month",
    "basin_distance_km",
)

FEATURE_UNITS: dict[str, str] = {
    "translation_speed_kmph": "km/h, from IBTrACS STORM_SPEED in knots",
    "bearing_deg": "degrees, from STORM_DIR",
    "distance_to_land_km": "km, from DIST2LAND in nautical miles",
    "latitude": "degrees north, at the storm's peak-wind fix",
    "longitude": "degrees east, at the storm's peak-wind fix",
    "month": "1-12, from ISO_TIME",
    "basin_distance_km": "km from the study area's centre",
}

#: One storm per row. The lifetime peak is the target; the features are read at
#: the fix where that peak occurred, which is the moment the prediction is about.
@dataclass(frozen=True)
class TrainingRow:
    sid: str
    name: str
    season: int
    features: tuple[float, ...]
    target_kt: float


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def build_training_set(since: int = 1970) -> tuple[TrainingRow, ...]:
    """One row per North Indian Ocean storm at or after `since`.

    **The target is `max(USA_WIND)` over the storm's lifetime**, which needs no
    interpretation of the `LANDFALL` column at all — see the module note for why
    that matters.

    A storm with no non-negative `USA_WIND` is absent rather than zero-filled:
    a row whose target were 0 would teach the model that an unmeasured storm is
    a calm one, which is the mistake this project has already made once
    (MEMORY.md §31).
    """
    per_storm: dict[str, list[dict]] = {}
    for row in read_ni_rows(DEFAULT_IBTRACS_PATH):
        season = ibtracs_number(row.get("SEASON"))
        if season is None or int(season) < since:
            continue
        per_storm.setdefault(row["SID"], []).append(row)

    out: list[TrainingRow] = []
    for sid, rows in sorted(per_storm.items()):
        rows.sort(key=lambda r: r.get("ISO_TIME") or "")

        peak_kt: float | None = None
        peak_index = -1
        for index, row in enumerate(rows):
            wind = ibtracs_number(row.get("USA_WIND"))
            if wind is None:
                continue
            if peak_kt is None or wind > peak_kt:
                peak_kt = wind
                peak_index = index

        if peak_kt is None or peak_index < 0:
            continue

        at = rows[peak_index]
        latitude = ibtracs_number(at.get("LAT"))
        longitude = ibtracs_number(at.get("LON"))
        # A fix with no position cannot contribute features. Dropped rather than
        # defaulted: a fabricated 0.0 would be off by 21 degrees.
        if latitude is None or longitude is None:
            continue

        speed_kt = ibtracs_number(at.get("STORM_SPEED"))
        bearing = ibtracs_number(at.get("STORM_DIR"))
        distance_nm = ibtracs_number(at.get("DIST2LAND"))
        month_text = (at.get("ISO_TIME") or "")[5:7]
        month = int(month_text) if month_text.isdigit() else 0

        features = (
            (speed_kt * KNOTS_TO_KMPH) if speed_kt is not None else 0.0,
            bearing if bearing is not None else 0.0,
            (distance_nm * NM_TO_KM) if distance_nm is not None else 0.0,
            latitude,
            longitude,
            float(month),
            _haversine_km(latitude, longitude, STUDY_CENTRE[0], STUDY_CENTRE[1]),
        )
        if not all(math.isfinite(v) for v in features):
            continue

        out.append(
            TrainingRow(
                sid=sid,
                name=at.get("NAME") or "UNNAMED",
                season=int(season),
                features=features,
                target_kt=peak_kt,
            )
        )
    return tuple(out)


def median_baseline_mae(rows: tuple[TrainingRow, ...]) -> float:
    """LOOCV MAE of always predicting the median of the storms you can see.

    The number the model has to beat. With a few hundred rows and a median near
    50 kt, a flat line is a genuinely competitive forecast, and a model that
    cannot beat it is not a model.

    The median is recomputed **inside every fold**, from the training storms
    only. Using the whole-set median would hand the baseline a peek at the held
    out storm and make the comparison flattering to the baseline — which would
    be the wrong way for a gate to err. It happens not to matter here (both
    come to the same figure, because a median over a few hundred rows barely
    moves when one row is removed), but the fair version costs nothing and the flattering one
    would be a latent trap the next time the data changed.
    """
    if len(rows) < 2:
        return float("nan")
    y = np.array([r.target_kt for r in rows], dtype=float)
    errors = []
    for train_index, test_index in LeaveOneOut().split(y.reshape(-1, 1)):
        errors.append(abs(y[test_index][0] - float(np.median(y[train_index]))))
    return float(np.mean(errors))


@dataclass(frozen=True)
class LeaveOneOutReport:
    n: int
    mae_kt: float
    baseline_mae_kt: float
    r2: float
    beats_baseline: bool
    per_fold: tuple[float, ...]

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "mae_kt": round(self.mae_kt, 3),
            "baseline_mae_kt": round(self.baseline_mae_kt, 3),
            "r2": round(self.r2, 4),
            "beats_baseline": self.beats_baseline,
            "limitation": (
                f"Leave-one-out cross-validation over {self.n} North Indian Ocean "
                f"storms: mean absolute error {self.mae_kt:.1f} kt against a flat "
                f"median baseline of {self.baseline_mae_kt:.1f} kt. This is a "
                f"statistical estimate about past storms, not a forecast about any "
                f"future one, and it is not the surge figure — that comes from the "
                f"deterministic law and remains authoritative."
            ),
        }


def evaluate(rows: tuple[TrainingRow, ...]) -> LeaveOneOutReport:
    """Leave-one-out CV. Every storm is predicted by a model that never saw it.

    Ridge with a cross-validated penalty rather than plain OLS: with a few
    hundred rows and eight correlated features, an unpenalised fit chases the
    fold noise, which is exactly what LOOCV is there to expose.
    """
    if len(rows) < 3:
        raise ValueError(f"need at least 3 storms to evaluate, got {len(rows)}")
    X = np.array([r.features for r in rows], dtype=float)
    y = np.array([r.target_kt for r in rows], dtype=float)

    predictions = np.empty_like(y)
    for train_index, test_index in LeaveOneOut().split(X):
        model = Pipeline(
            [("scale", StandardScaler()), ("ridge", RidgeCV(alphas=np.logspace(-2, 3, 24)))]
        )
        model.fit(X[train_index], y[train_index])
        predictions[test_index] = model.predict(X[test_index])

    errors = np.abs(y - predictions)
    baseline = median_baseline_mae(rows)
    mae = float(errors.mean())
    residual = y - predictions
    total = y - y.mean()
    # A flat-line's R^2 is 0 by construction, so this is a fair comparison.
    r2 = float(1.0 - (residual**2).sum() / (total**2).sum()) if (total**2).sum() else 0.0
    return LeaveOneOutReport(
        n=len(rows),
        mae_kt=mae,
        baseline_mae_kt=baseline,
        r2=r2,
        beats_baseline=mae < baseline,
        per_fold=tuple(float(e) for e in errors),
    )


#: Which figure `StormPeakEstimate.estimate_kt` actually holds.
SOURCE_MODEL = "model"
SOURCE_BASELINE = "median_baseline"
SOURCE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StormPeakEstimate:
    """One storm's figure, and an unambiguous statement of where it came from.

    **`estimate_kt` is what the app shows. `estimate_source` says what it is.**
    When the gate failed, `estimate_kt` *is* the baseline median and the model's
    own output is preserved in `model_kt` for the record — never presented as the
    answer, because the evidence says it is worse than a flat line.
    """

    estimate_kt: float
    estimate_source: str
    model_kt: float | None
    baseline_kt: float
    interval_kt: tuple[float, float]
    n_training: int
    beats_baseline: bool
    limitation: str

    @property
    def is_a_prediction(self) -> bool:
        return self.estimate_source == SOURCE_MODEL

    def to_dict(self) -> dict:
        return {
            "estimate_kt": round(self.estimate_kt, 1),
            "estimate_source": self.estimate_source,
            "is_a_prediction": self.is_a_prediction,
            # Kept, and named for what it is: the losing model's output. A client
            # that renders this as the headline number has misread the gate.
            "model_kt_unused": None if self.model_kt is None else round(self.model_kt, 1),
            "baseline_kt": round(self.baseline_kt, 1),
            "interval_kt": (
                None
                if any(not math.isfinite(v) for v in self.interval_kt)
                else [round(v, 1) for v in self.interval_kt]
            ),
            "n_training": self.n_training,
            "beats_baseline": self.beats_baseline,
            "unit": "knots, 1-minute mean sustained (IBTrACS USA_WIND)",
            "limitation": self.limitation,
        }


class StormPeakIntensityModel:
    """Ridge on standardised features, with the gate attached to the estimate."""

    def __init__(self) -> None:
        self._pipeline: Pipeline | None = None
        self.report: LeaveOneOutReport | None = None
        self._median = float("nan")

    def fit(self, rows: tuple[TrainingRow, ...]) -> "StormPeakIntensityModel":
        X = np.array([r.features for r in rows], dtype=float)
        y = np.array([r.target_kt for r in rows], dtype=float)
        self._pipeline = Pipeline(
            [("scale", StandardScaler()), ("ridge", RidgeCV(alphas=np.logspace(-2, 3, 24)))]
        )
        self._pipeline.fit(X, y)
        self._median = float(np.median(y))
        self.report = evaluate(rows)
        return self

    def predict(self, features: tuple[float, ...]) -> float | None:
        """A prediction in knots, or `None` for anything unusable.

        `None` rather than a clamped number for a non-finite feature: a NaN
        would propagate through the scaler into a plausible-looking wrong value.
        """
        if self._pipeline is None:
            raise RuntimeError("fit() before predict()")
        values = tuple(float(v) for v in features)
        if len(values) != len(FEATURE_NAMES) or not all(math.isfinite(v) for v in values):
            return None
        return float(self._pipeline.predict(np.array([values]))[0])

    @property
    def median_kt(self) -> float:
        """The training median, and the figure that ships when the gate fails."""
        return self._median

    def to_json(self, path: Path) -> None:
        if self._pipeline is None:
            raise RuntimeError("fit() before to_json()")
        scaler: StandardScaler = self._pipeline.named_steps["scale"]
        ridge: RidgeCV = self._pipeline.named_steps["ridge"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "feature_names": list(FEATURE_NAMES),
                    "feature_units": FEATURE_UNITS,
                    "scaler_mean": scaler.mean_.tolist(),
                    "scaler_scale": scaler.scale_.tolist(),
                    "coefficients": ridge.coef_.tolist(),
                    "intercept": float(ridge.intercept_),
                    "alpha": float(ridge.alpha_),
                    "median_kt": self._median,
                    "report": self.report.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, path: Path) -> "StormPeakIntensityModel":
        payload = json.loads(path.read_text())
        if tuple(payload["feature_names"]) != FEATURE_NAMES:
            raise ValueError(
                f"{path} was written for features {payload['feature_names']}, but this "
                f"code expects {list(FEATURE_NAMES)}. Retrain rather than guess."
            )
        model = cls()
        scaler = StandardScaler()
        scaler.mean_ = np.array(payload["scaler_mean"], dtype=float)
        scaler.scale_ = np.array(payload["scaler_scale"], dtype=float)
        ridge = RidgeCV(alphas=[1.0])
        ridge.coef_ = np.array(payload["coefficients"], dtype=float)
        ridge.intercept_ = float(payload["intercept"])
        model._pipeline = Pipeline([("scale", scaler), ("ridge", ridge)])
        model._median = float(payload["median_kt"])
        return model


@lru_cache(maxsize=1)
def trained_model() -> tuple[StormPeakIntensityModel, LeaveOneOutReport]:
    """The fitted model, or the gate's own failure.

    `beats_baseline` is carried on the estimate, not resolved here: a model that
    lost is still worth shipping as long as it says it lost, because the number
    is only ever shown next to the disclosure.
    """
    rows = build_training_set()
    model = StormPeakIntensityModel().fit(rows)
    return model, model.report


def estimate_for(features: tuple[float, ...]) -> StormPeakEstimate:
    """The figure for one set of features, gated — and the gate is binding.

    **When `beats_baseline` is false, `estimate_kt` is the flat median, not the
    model's output.** That is the whole point of running the gate: a model that
    loses to a one-line constant has not earned the right to be the number on
    screen, however plausible it looks. The losing output stays available in
    `model_kt` because suppressing it entirely would hide the evidence.

    This is the outcome on the real data: leave-one-out MAE 21.94 kt against a
    flat median's 21.03 kt. The features carry almost no signal about how strong
    a North Indian Ocean storm will get — the strongest of the seven correlates
    with the target at 0.21. The honest product is therefore a constant, labelled
    as a constant, and a record of why.
    """
    model, report = trained_model()
    median_kt = model.median_kt
    prediction = model.predict(features)

    if prediction is None:
        return StormPeakEstimate(
            estimate_kt=median_kt,
            estimate_source=SOURCE_UNAVAILABLE,
            model_kt=None,
            baseline_kt=median_kt,
            interval_kt=(float("nan"), float("nan")),
            n_training=report.n,
            beats_baseline=False,
            limitation=(
                "No estimate. The supplied features were unusable, so the training "
                "median is reported and nothing should be read into it."
            ),
        )

    if report.beats_baseline:
        spread = report.baseline_mae_kt
        return StormPeakEstimate(
            estimate_kt=prediction,
            estimate_source=SOURCE_MODEL,
            model_kt=prediction,
            baseline_kt=median_kt,
            interval_kt=(prediction - spread, prediction + spread),
            n_training=report.n,
            beats_baseline=True,
            limitation=(
                f"A machine-learning estimate of a storm's peak 1-minute mean wind, "
                f"in knots. It beats a flat median baseline ({report.mae_kt:.1f} kt "
                f"against {report.baseline_mae_kt:.1f} kt, leave-one-out over "
                f"{report.n} storms), so it is a usable estimate. It is a statistical "
                f"statement about past North Indian Ocean storms, not a forecast "
                f"about any future one. **It is not the surge figure** — that comes "
                f"from the deterministic law 1.2 x (wind/115)^2 and stays "
                f"authoritative."
            ),
        )

    return StormPeakEstimate(
        estimate_kt=median_kt,
        estimate_source=SOURCE_BASELINE,
        model_kt=prediction,
        baseline_kt=median_kt,
        interval_kt=(median_kt - report.baseline_mae_kt, median_kt + report.baseline_mae_kt),
        n_training=report.n,
        beats_baseline=False,
        limitation=(
            f"The figure below is NOT a prediction and no model produced it. It is "
            f"the flat median peak wind of the {report.n} North Indian Ocean storms "
            f"in the training set. A ridge model on seven real IBTrACS features was "
            f"trained to beat it and did not: leave-one-out mean absolute error "
            f"{report.mae_kt:.1f} kt against the baseline's {report.baseline_mae_kt:.1f} kt "
            f"(R2 {report.r2:.2f}). Those features carry almost no signal about how "
            f"strong a storm will get in this basin, so the baseline ships and the "
            f"model's output is recorded but not shown. **This is not the surge "
            f"figure** — that comes from the deterministic law 1.2 x (wind/115)^2 "
            f"and stays authoritative."
        ),
    )


__all__ = [
    "FEATURE_NAMES",
    "FEATURE_UNITS",
    "SOURCE_BASELINE",
    "SOURCE_MODEL",
    "SOURCE_UNAVAILABLE",
    "KNOTS_TO_KMPH",
    "LeaveOneOutReport",
    "NM_TO_KM",
    "STUDY_CENTRE",
    "StormPeakEstimate",
    "StormPeakIntensityModel",
    "TrainingRow",
    "build_training_set",
    "estimate_for",
    "evaluate",
    "median_baseline_mae",
    "trained_model",
]