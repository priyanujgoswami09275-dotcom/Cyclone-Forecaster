"""Tests for the RETIRED 3-feature surge regression.

These guard the record in `README.md`, not any runtime behaviour — nothing in
`backend/` imports this model any more. They exist so the claim that
displaced it ("a trained regression over a scaling law") stays checkable
rather than being taken on trust.

The headline test is `test_the_regression_loses_to_a_mean_baseline`. It is the
single most damning fact about the retired model and it is a one-line
computation, so it should be verified rather than asserted in prose.
"""

import numpy as np
import pytest
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import LeaveOneOut, cross_val_predict

from backend.experiments.surge_regression.train_surge_model import (
    NAMES,
    train,
    training_table,
)


def test_training_table_matches_spec():
    X, y = training_table()
    assert X.shape == (4, 3) and y.shape == (4,)
    assert list(X[0]) == [115, 16, 1] and y[0] == 1.2  # Remal, anchor case
    assert list(X[3]) == [70, 14, 0] and y[3] == 0.6  # Mandous


def test_loo_mae_finite_and_full_fit_interpolates_remal():
    X, y = training_table()
    model, mae = train(X, y)
    assert np.isfinite(mae) and mae >= 0
    # 4 points / 4 dof (3 features + intercept) => full fit interpolates Remal
    assert abs(model.predict([[115, 16, 1]])[0] - 1.2) < 0.1


def test_the_fit_is_exactly_determined():
    """4 rows, 3 feature columns, 4 parameters => zero residual dof.

    This is why the model cannot be wrong in-sample, and therefore has no
    redundancy left to spend on generalisation.
    """
    X, y = training_table()
    n_rows, n_features = X.shape
    n_parameters = n_features + 1  # coefficients + intercept
    assert n_parameters == n_rows == 4
    assert n_rows - n_parameters == 0

    model, _ = train(X, y)
    residuals = y - model.predict(X)
    assert np.allclose(residuals, 0.0, atol=1e-9)


def test_loo_mae_is_2_36_m():
    """The figure quoted in README.md and MEMORY.md."""
    X, y = training_table()
    _, mae = train(X, y)
    assert mae == pytest.approx(2.36, abs=0.01)


def test_the_regression_loses_to_a_mean_baseline():
    """A predictor that ignores its inputs beats it. This is the reason it went.

    Compared like-for-like: both are leave-one-out cross-validated, so neither
    sees the point it is predicting. A DummyRegressor(strategy="mean") is the
    standard sklearn baseline — it predicts the mean of the training folds.
    """
    X, y = training_table()

    reg_mae = float(np.mean(np.abs(
        cross_val_predict(LinearRegression(), X, y, cv=LeaveOneOut()) - y)))
    base_mae = float(np.mean(np.abs(
        cross_val_predict(DummyRegressor(strategy="mean"), X, y, cv=LeaveOneOut()) - y)))

    # The quoted comparison: regression 2.36 m vs baseline 0.90 m.
    assert base_mae == pytest.approx(0.90, abs=0.01)
    assert base_mae < reg_mae
    assert reg_mae / base_mae == pytest.approx(2.62, abs=0.05)


def test_every_training_point_is_wrong_by_roughly_its_own_magnitude():
    """Per-point LOOCV errors, not the mean — the mean hides this."""
    X, y = training_table()
    preds = cross_val_predict(LinearRegression(), X, y, cv=LeaveOneOut())
    errors = np.abs(preds - y)

    expected = {
        "Remal 2024": 1.479,
        "Helen 2013": 1.581,
        "Lehar 2013": 2.926,
        "Mandous 2021": 3.453,
    }
    for name, err in zip(NAMES, errors):
        assert err == pytest.approx(expected[name], abs=0.01), name

    # No point is predicted to better than half its own value.
    assert all(err > 0.5 * yi for err, yi in zip(errors, y))
    # And two of the predictions are physically absurd.
    assert min(preds) < 0.0  # Lehar -> -0.026 m
    assert max(preds) > 4.0  # Mandous -> 4.053 m from a 0.6 m event
