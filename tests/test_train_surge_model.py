"""Tests for backend/data_pipeline/train_surge_model.py."""
import numpy as np

from backend.data_pipeline.train_surge_model import train, training_table


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
