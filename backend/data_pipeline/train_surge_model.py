"""Train the surge regression model -> data/surge_model.pkl + print LOOCV MAE.

Features: [wind_kmph, forward_speed_kmph, approach_angle_flag] -> surge (m).

Training points are the four verified real historical cases from CLAUDE.md
(source: project brief; surge values as documented there — Remal ~1.0-1.5 m
range anchored at 1.2 m; Helen/Lehar/Mandous from the same verified set).
Approach angle is a 0/1 flag (1 = head-on/orthogonal landfall like Remal/Lehar,
0 = oblique like Helen/Mandous). More points from the RSMC New Delhi bulletin
archive are a documented stretch item (Task.md Module A) — when added, append
rows here, do not change the feature order.

Evaluation: leave-one-out CV (n=4 << any k-fold makes sense). NOTE: with
4 points and 3 features the FULL fit interpolates exactly (4 dof), so the
LOOCV MAE is the only honest error estimate — treat it as such, not as a
measure of real-world accuracy on unseen regimes.
"""

import joblib
import numpy as np
from pathlib import Path
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import LeaveOneOut, cross_val_predict

OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "surge_model.pkl"
FEATURES = ["wind_kmph", "forward_speed_kmph", "approach_angle_flag"]
NAMES = ["Remal 2024", "Helen 2013", "Lehar 2013", "Mandous 2021"]


def training_table() -> tuple[np.ndarray, np.ndarray]:
    """The 4 verified real training points from CLAUDE.md (see module docstring)."""
    X = np.array(
        [
            [115, 16, 1],  # Remal   -> 1.2 m (observed ~1.0-1.5, anchored 1.2)
            [105, 13, 0],  # Helen   -> 1.6 m
            [95, 20, 1],   # Lehar   -> 2.9 m
            [70, 14, 0],   # Mandous -> 0.6 m
        ],
        dtype=float,
    )
    y = np.array([1.2, 1.6, 2.9, 0.6], dtype=float)
    return X, y


def train(X: np.ndarray, y: np.ndarray) -> tuple[LinearRegression, float]:
    """Full-fit LinearRegression + honest LOOCV mean absolute error."""
    loo = LeaveOneOut()
    preds = cross_val_predict(LinearRegression(), X, y, cv=loo)
    loo_mae = float(np.mean(np.abs(preds - y)))
    model = LinearRegression().fit(X, y)
    return model, loo_mae


def main() -> None:
    X, y = training_table()
    model, loo_mae = train(X, y)
    loo_preds = cross_val_predict(LinearRegression(), X, y, cv=LeaveOneOut())
    for name, xi, yi, pi in zip(NAMES, X, y, loo_preds):
        print(f"  {name:<12} actual {yi:.1f} m | LOO prediction {pi:.2f} m")
    print(f"LOOCV MAE: {loo_mae:.2f} m")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": FEATURES, "loo_mae": loo_mae}, OUT_PATH)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
