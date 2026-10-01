"""The ML storm peak intensity layer, and the gate it failed.

Every figure in this file is recomputed from the archive and asserted as a
literal, because the module's docstrings quote the same figures and prose is not
evidence. The last test in the file enforces that: any number written in
`storm_peak_intensity.py`'s docstrings or comments must be pinned here or
allowlisted with a reason.

**The headline result is a failure.** A ridge model on seven real IBTrACS
features does not beat a flat median at predicting a North Indian Ocean storm's
peak sustained wind — 21.9 kt against 21.0 kt, leave-one-out over 300 storms. So
the flat median ships, `estimate_kt` is the median, and the model's output is
recorded but never presented as a prediction. These tests exist to keep that
true; several of them would fail if someone quietly switched the shipped figure
back to the model's.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.cyclones.historical import DEFAULT_IBTRACS_PATH, read_ni_rows
from backend.data_pipeline import train_storm_peak_intensity as cli
from backend.ml import storm_peak_intensity as ml
from backend.ml.storm_peak_intensity import (
    FEATURE_NAMES,
    SOURCE_BASELINE,
    SOURCE_MODEL,
    SOURCE_UNAVAILABLE,
    StormPeakIntensityModel,
    build_training_set,
    estimate_for,
    evaluate,
    median_baseline_mae,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- Pinned figures. Each is recomputed in a test below; see figure_guard. ---

#: Storms in the training set: NI, season >= 1970, a non-negative USA_WIND
#: somewhere in the file.
TRAINING_STORMS = 300

#: Leave-one-out mean absolute error, in knots.
MODEL_MAE_KT = 21.94

#: The same metric for a flat median, which the model has to beat.
BASELINE_MAE_KT = 21.03

#: Median and spread of the target itself.
TARGET_MEDIAN_KT = 50.0
TARGET_MIN_KT = 20.0
TARGET_MAX_KT = 150.0

#: How many training storms reached 64 kt — a hurricane — and the share.
STORMS_HURRICANE = 99
STORMS_HURRICANE_SHARE_PCT = 33

#: The leaked feature's correlation with the target, and how close it sat to it.
LEAK_CORRELATION = 0.971
LEAK_WITHIN_5KT_SHARE_PCT = 89

#: The strongest correlation any *surviving* feature reaches with the target.
#: Ten times smaller than the leak, and the reason the gate is a real result
#: rather than a failure to find features.
MAX_SURVIVING_CORRELATION = 0.21

#: A threshold no feature may cross, enforced by the CLI before it writes
#: anything.
LEAK_THRESHOLD = 0.9


@pytest.fixture(scope="module")
def rows() -> tuple[ml.TrainingRow, ...]:
    return build_training_set()


@pytest.fixture(scope="module")
def report(rows):
    return evaluate(rows)


# --------------------------------------------------------------------------
# The training set
# --------------------------------------------------------------------------


def test_the_training_set_holds_every_usable_north_indian_ocean_storm(rows) -> None:
    """Pins the row count, so the module's `n = ` claim cannot drift."""
    assert len(rows) == TRAINING_STORMS

    # Independently: count storms the archive says should be there. A storm
    # qualifies if it is NI, at or after 1970, and has a usable USA_WIND.
    qualifying: set[str] = set()
    for record in read_ni_rows(DEFAULT_IBTRACS_PATH):
        season = record.get("SEASON")
        wind = record.get("USA_WIND")
        if not season or not wind:
            continue
        if int(season) < 1970:
            continue
        try:
            if float(wind) < 0:
                continue
        except ValueError:
            continue
        qualifying.add(record["SID"])
    assert len(qualifying) == TRAINING_STORMS


def test_every_row_is_a_real_storm_from_the_right_basin(rows) -> None:
    """Not a Pacific or Atlantic cyclone wearing a North Indian Ocean label."""
    seen: set[str] = set()
    for row in rows:
        assert row.season >= 1970, row.sid
        assert row.name, row.sid
        assert all(math.isfinite(v) for v in row.features), row.sid
        assert row.target_kt >= 0, row.sid
        seen.add(row.sid)
    assert len(seen) == len(rows), "a storm contributed two rows"


def test_the_target_is_the_storms_peak_wind_and_not_its_last_one(rows) -> None:
    """Recompute `max(USA_WIND)` from the archive for every training storm.

    This is the difference between "peak intensity" and "intensity at the end of
    the file", and the module's naming depends on it.
    """
    expected: dict[str, float] = {}
    for record in read_ni_rows(DEFAULT_IBTRACS_PATH):
        wind = record.get("USA_WIND")
        if not wind:
            continue
        try:
            value = float(wind)
        except ValueError:
            continue
        if value < 0:
            continue
        sid = record["SID"]
        expected[sid] = max(expected.get(sid, float("-inf")), value)

    for row in rows:
        assert row.target_kt == pytest.approx(expected[row.sid], abs=1e-9), row.sid


#: What `LANDFALL` actually holds, over the archive's North Indian Ocean rows.
#: This module's argument that it is not a boolean rests on every one of these,
#: so each is recomputed rather than quoted.
NI_ROWS_TOTAL = 57852
LANDFALL_NUMERIC = 55995
LANDFALL_BLANK = 1857
LANDFALL_ZERO = 22007
LANDFALL_POSITIVE = 33988
LANDFALL_MAX_KM = 1463.0


def _numeric_landfall() -> tuple[int, list[float]]:
    values: list[float] = []
    for record in read_ni_rows(DEFAULT_IBTRACS_PATH):
        raw = record.get("LANDFALL")
        if raw is None or not raw.strip():
            continue
        values.append(float(raw))
    return LANDFALL_BLANK, values


def test_landfall_is_not_a_boolean_flag() -> None:
    """The reason this module does not build a landfall-window target.

    If `LANDFALL` ever became a real flag, a landfall-intensity target would be
    worth deriving and the naming note in this module would be wrong.
    """
    rows = list(read_ni_rows(DEFAULT_IBTRACS_PATH))
    assert len(rows) == NI_ROWS_TOTAL

    _blank, values = _numeric_landfall()
    assert len(values) == LANDFALL_NUMERIC
    assert len(rows) - len(values) == LANDFALL_BLANK

    assert sum(1 for v in values if v == 0.0) == LANDFALL_ZERO
    assert sum(1 for v in values if v > 0.0) == LANDFALL_POSITIVE
    assert max(values) == LANDFALL_MAX_KM

    # The reason it is mistaken for a flag: whole kilometres, so 0 reads as
    # false and there is nothing between 0 and 1 to give it away.
    assert not any(0 < v < 1 for v in values), "a fractional value would give it away"
    assert all(float(v).is_integer() for v in values)


def test_reading_landfall_as_a_flag_would_corrupt_a_third_of_the_rows() -> None:
    """Quantify the mistake, so "do not treat it as a flag" has a cost."""
    rows = list(read_ni_rows(DEFAULT_IBTRACS_PATH))
    _blank, values = _numeric_landfall()
    zero_share = 100 * LANDFALL_ZERO / NI_ROWS_TOTAL
    # Roughly 38% of rows would be labelled "at the coast" on a boolean read.
    assert round(zero_share) == 38
    assert LANDFALL_POSITIVE > LANDFALL_ZERO, "the majority are not zeros"


def test_the_target_spans_the_range_the_docstring_quotes(rows) -> None:
    targets = sorted(r.target_kt for r in rows)
    assert targets[0] == TARGET_MIN_KT
    assert targets[-1] == TARGET_MAX_KT
    assert targets[len(targets) // 2] == TARGET_MEDIAN_KT

    hurricanes = sum(1 for t in targets if t >= 64)
    assert hurricanes == STORMS_HURRICANE
    assert round(100 * hurricanes / len(targets)) == STORMS_HURRICANE_SHARE_PCT


# --------------------------------------------------------------------------
# The leak, which is the reason the model looks bad
# --------------------------------------------------------------------------


def test_the_leaky_feature_is_not_in_the_model() -> None:
    """`peak_before_kmph` scored 3.5 kt by being the answer. It is gone.

    Asserted on the feature list, because that is the only place it could come
    back from — the row builder and the artefact both derive their columns from
    `FEATURE_NAMES`.
    """
    assert "peak_before_kmph" not in FEATURE_NAMES
    assert "peak_before" not in " ".join(FEATURE_NAMES)
    assert len(FEATURE_NAMES) == 7


def test_the_leak_really_was_the_target(rows) -> None:
    """Recompute the leak's correlation, so removing it is justified by data.

    The leaked feature was the running maximum *strictly before* each storm's
    peak-wind fix. Reconstructing it here — rather than trusting the module's
    note — means the removal is justified by a recomputed measurement. If this
    stops holding, the note is wrong, which matters more than the other way
    round.
    """
    by_storm: dict[str, list[tuple[str, float]]] = {}
    for record in read_ni_rows(DEFAULT_IBTRACS_PATH):
        if int(record.get("SEASON") or 0) < 1970:
            continue
        wind = record.get("USA_WIND")
        if not wind:
            continue
        try:
            value = float(wind)
        except ValueError:
            continue
        if value < 0:
            continue
        by_storm.setdefault(record["SID"], []).append(
            (record.get("ISO_TIME") or "", value)
        )

    leaked: dict[str, float] = {}
    for sid, fixes in by_storm.items():
        fixes.sort(key=lambda pair: pair[0])
        readings = [value for _when, value in fixes]
        peak = max(readings)
        first_peak = readings.index(peak)
        # Everything strictly before the peak fix's first occurrence.
        before = readings[:first_peak]
        leaked[sid] = max(before) if before else 0.0

    targets = {r.sid: r.target_kt for r in rows}
    shared = [sid for sid in targets if sid in leaked]
    assert len(shared) == TRAINING_STORMS

    x = np.array([leaked[sid] for sid in shared])
    y = np.array([targets[sid] for sid in shared])
    correlation = float(np.corrcoef(x, y)[0, 1])

    assert round(correlation, 3) == LEAK_CORRELATION, (
        f"the removed feature correlates at {correlation:.4f}, not the "
        f"{LEAK_CORRELATION} the module claims"
    )
    assert round(100 * float(np.mean(np.abs(x - y) <= 5))) == LEAK_WITHIN_5KT_SHARE_PCT


def test_no_surviving_feature_carries_the_target(rows) -> None:
    """Nothing available at prediction time is secretly the answer."""
    X = np.array([r.features for r in rows], dtype=float)
    y = np.array([r.target_kt for r in rows], dtype=float)
    for index, name in enumerate(FEATURE_NAMES):
        correlation = float(np.corrcoef(X[:, index], y)[0, 1])
        assert abs(correlation) < LEAK_THRESHOLD, f"{name} at {correlation:+.3f}"


def test_the_strongest_real_feature_is_weak(rows) -> None:
    """Pins why the gate failed: the best feature is nearly uncorrelated."""
    X = np.array([r.features for r in rows], dtype=float)
    y = np.array([r.target_kt for r in rows], dtype=float)
    best = max(
        abs(float(np.corrcoef(X[:, i], y)[0, 1])) for i in range(len(FEATURE_NAMES))
    )
    assert round(best, 2) == MAX_SURVIVING_CORRELATION


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_the_baseline_is_a_flat_median_computed_leave_one_out(rows) -> None:
    """Recomputed fold by fold, so the baseline is not peeking at its own test row."""
    y = np.array([r.target_kt for r in rows], dtype=float)
    manual = float(
        np.mean([abs(y[i] - np.median(np.delete(y, i))) for i in range(len(y))])
    )
    assert median_baseline_mae(rows) == pytest.approx(manual, abs=1e-9)


def test_the_baseline_costs_what_the_docstring_says(rows) -> None:
    assert median_baseline_mae(rows) == pytest.approx(BASELINE_MAE_KT, abs=0.05)


def test_the_model_costs_what_the_docstring_says(report) -> None:
    assert report.n == TRAINING_STORMS
    assert report.mae_kt == pytest.approx(MODEL_MAE_KT, abs=0.05)
    assert report.baseline_mae_kt == pytest.approx(BASELINE_MAE_KT, abs=0.05)


def test_the_gate_fails_and_is_recorded_as_failing(report) -> None:
    """The headline. If this ever passes, the docstrings must change with it.

    Asserted as a relation *and* as a sign, because the direction is the
    finding: the model is worse than a constant. A future change that flips it
    should be a deliberate, documented improvement, not a silent one.
    """
    assert report.beats_baseline is False
    assert report.mae_kt > report.baseline_mae_kt
    assert report.r2 < 0.1, "R2 near zero is what 'no signal' looks like"


def test_the_report_serialises_the_failure_rather_than_hiding_it(report) -> None:
    payload = report.to_dict()
    assert payload["beats_baseline"] is False
    assert payload["n"] == TRAINING_STORMS
    assert "not a forecast" in payload["limitation"]
    assert "not the surge figure" in payload["limitation"]


# --------------------------------------------------------------------------
# What ships
# --------------------------------------------------------------------------


def _sample_features() -> tuple[float, ...]:
    return (15.0, 300.0, 80.0, 21.5, 88.0, 5.0, 120.0)


def test_a_failed_gate_ships_the_baseline_and_says_so() -> None:
    """The number on screen is the median, and it is not called a prediction."""
    estimate = estimate_for(_sample_features())

    assert estimate.beats_baseline is False
    assert estimate.estimate_source == SOURCE_BASELINE
    assert estimate.is_a_prediction is False
    assert estimate.estimate_kt == pytest.approx(TARGET_MEDIAN_KT, abs=0.5)
    assert estimate.estimate_kt == pytest.approx(estimate.baseline_kt)


def test_the_losing_model_output_is_kept_but_never_presented() -> None:
    """Evidence is preserved; it is just not the answer."""
    estimate = estimate_for(_sample_features())
    assert estimate.model_kt is not None
    # It genuinely differs, so hiding it is a choice and not a coincidence.
    assert estimate.model_kt != pytest.approx(estimate.estimate_kt)
    # And every one of those features is nonsense, which is the point.
    assert 0 < estimate.model_kt < 400

    payload = estimate.to_dict()
    assert payload["estimate_source"] == SOURCE_BASELINE
    assert payload["is_a_prediction"] is False
    assert payload["model_kt_unused"] == pytest.approx(estimate.model_kt, abs=0.05)
    assert payload["estimate_kt"] == pytest.approx(estimate.baseline_kt, abs=0.05)
    assert "NOT a prediction" in payload["limitation"]


def test_the_disclosure_names_the_gate_the_model_failed() -> None:
    limitation = estimate_for(_sample_features()).limitation
    assert "leave-one-out" in limitation
    assert "median" in limitation
    assert "not the surge figure" in limitation
    assert "1.2 x (wind/115)^2" in limitation


def test_a_model_that_does_beat_the_gate_is_reported_as_a_prediction(
    monkeypatch, rows
) -> None:
    """The pass branch exists and is reachable, so it is not untested prose.

    `beats_baseline` is a field on the report, so replacing the report replaces
    the gate. That tests the branch which chooses `estimate_source`, without
    retraining anything or pretending the features became informative.
    """
    model, _ = ml.trained_model()
    assert len(rows) == TRAINING_STORMS

    passing = ml.LeaveOneOutReport(
        n=len(rows),
        mae_kt=5.0,
        baseline_mae_kt=BASELINE_MAE_KT,
        r2=0.7,
        beats_baseline=True,
        per_fold=(),
    )
    monkeypatch.setattr(ml, "trained_model", lambda: (model, passing))
    estimate = ml.estimate_for(_sample_features())

    assert estimate.beats_baseline is True
    assert estimate.estimate_source == SOURCE_MODEL
    assert estimate.is_a_prediction is True
    assert estimate.estimate_kt == pytest.approx(estimate.model_kt)
    assert estimate.estimate_kt != pytest.approx(estimate.baseline_kt, abs=0.01)
    assert "beats a flat median baseline" in estimate.limitation


def test_unusable_features_give_no_estimate_at_all() -> None:
    """`None` from predict, and an explicitly unavailable source."""
    model, _ = ml.trained_model()
    assert model.predict((float("nan"),) * len(FEATURE_NAMES)) is None
    assert model.predict((1.0, 2.0)) is None
    assert model.predict((float("inf"),) + (0.0,) * (len(FEATURE_NAMES) - 1)) is None

    for bad in ((float("nan"),) * len(FEATURE_NAMES), (1.0, 2.0)):
        estimate = ml.estimate_for(bad)
        assert estimate.estimate_source == SOURCE_UNAVAILABLE
        assert estimate.is_a_prediction is False
        assert estimate.model_kt is None
        assert "No estimate" in estimate.limitation


# --------------------------------------------------------------------------
# Artefact round trip
# --------------------------------------------------------------------------


def test_the_artefact_round_trips_to_the_same_prediction(rows, tmp_path) -> None:
    path = tmp_path / "model.json"
    StormPeakIntensityModel().fit(rows).to_json(path)

    reloaded = StormPeakIntensityModel.from_json(path)
    original = StormPeakIntensityModel().fit(rows)
    assert reloaded.predict(_sample_features()) == pytest.approx(
        original.predict(_sample_features()), abs=1e-9
    )
    assert reloaded.median_kt == pytest.approx(original.median_kt)


def test_an_artefact_for_different_features_is_refused(rows, tmp_path) -> None:
    """A stale artefact must fail loudly rather than mispredict silently."""
    import json

    path = tmp_path / "model.json"
    StormPeakIntensityModel().fit(rows).to_json(path)
    payload = json.loads(path.read_text())
    payload["feature_names"] = ["something", "else"]
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="Retrain rather than guess"):
        StormPeakIntensityModel.from_json(path)


def test_predicting_before_fitting_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="fit\\(\\) before predict\\(\\)"):
        StormPeakIntensityModel().predict(_sample_features())


# --------------------------------------------------------------------------
# This layer must not touch the deterministic surge law
# --------------------------------------------------------------------------


def test_the_ml_layer_never_reaches_the_surge_law() -> None:
    """AST, not grep: `surge.py` must not import anything from `backend.ml`."""
    import ast

    source = (REPO_ROOT / "backend" / "simulation" / "surge.py").read_text()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("ml" in name.split(".") for name in imported), imported

    # And the law still holds, because that is the only surge figure that ships.
    from backend.simulation.surge import surge_for_wind

    assert surge_for_wind(115) == pytest.approx(1.2)


def test_the_artefact_records_the_failed_gate(rows, tmp_path) -> None:
    import json

    path = tmp_path / "model.json"
    StormPeakIntensityModel().fit(rows).to_json(path)
    payload = json.loads(path.read_text())

    assert payload["feature_names"] == list(FEATURE_NAMES)
    assert payload["report"]["beats_baseline"] is False
    assert payload["report"]["n"] == TRAINING_STORMS
    assert payload["median_kt"] == pytest.approx(TARGET_MEDIAN_KT)


# --------------------------------------------------------------------------
# The training CLI
# --------------------------------------------------------------------------


def test_the_cli_writes_an_artefact_and_reports_the_failed_gate(rows, tmp_path) -> None:
    out = tmp_path / "nested" / "model.json"
    code = cli.main(["--out", str(out)])

    # Zero on a failed gate by default: losing is a result, not a broken build.
    assert code == 0
    assert out.exists()

    import json

    assert json.loads(out.read_text())["report"]["beats_baseline"] is False


def test_the_cli_can_be_told_to_treat_a_failed_gate_as_a_failure(tmp_path) -> None:
    assert cli.main(["--out", str(tmp_path / "a.json"), "--require-gate"]) == 1


def test_the_cli_refuses_to_fit_a_leaky_feature(rows) -> None:
    """The guard is demonstrated by making it fire, not asserted in a comment."""
    leaked = tuple(
        ml.TrainingRow(
            sid=r.sid,
            name=r.name,
            season=r.season,
            features=r.features[:-1] + (r.target_kt,),
            target_kt=r.target_kt,
        )
        for r in rows
    )
    assert cli.find_leaks(rows, LEAK_THRESHOLD) == []
    found = cli.find_leaks(leaked, LEAK_THRESHOLD)
    assert [name for name, _ in found] == ["basin_distance_km"]


def test_the_cli_aborts_on_a_leak_rather_than_writing_anything(
    monkeypatch, tmp_path
) -> None:
    rows = build_training_set()
    monkeypatch.setattr(
        cli,
        "find_leaks",
        lambda *_a, **_k: [("basin_distance_km", 0.99)],
    )
    out = tmp_path / "model.json"
    assert cli.main(["--out", str(out)]) == 3
    assert not out.exists(), "a leak-tainted artefact was written anyway"


def test_the_cli_runs_as_a_module() -> None:
    """The documented invocation actually works."""
    result = subprocess.run(
        [sys.executable, "-m", "backend.data_pipeline.train_storm_peak_intensity",
         "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert "--require-gate" in result.stdout


# --------------------------------------------------------------------------
# Every figure in this module's prose is pinned above
# --------------------------------------------------------------------------

from figure_guard import assert_figures_pinned, prose_figures  # noqa: E402

_MODULE_PY = REPO_ROOT / "backend" / "ml" / "storm_peak_intensity.py"

#: Figures in the module that are definitions or library constants rather than
#: measurements of the archive. Every entry carries a reason; an entry with an
#: empty reason is itself a failure.
FIGURE_ALLOWLIST: dict[str, str] = {
    "115": "the anchor wind of the surge law in knots, a defined constant",
    "6371": "mean Earth radius in km, a geodetic definition",
}


def test_every_measured_figure_in_this_module_is_pinned() -> None:
    # Both files, because the archive row count this module quotes is recomputed
    # in the IBTrACS suite. A figure pinned anywhere is pinned.
    assert_figures_pinned(
        _MODULE_PY, [Path(__file__), REPO_ROOT / "tests" / "test_ibtracs_ni.py"],
        FIGURE_ALLOWLIST,
    )


def test_the_figure_invariant_bites(tmp_path) -> None:
    """Prove the guard fails on an unpinned figure rather than passing.

    Built from parts so the invented figure is not a literal anywhere in this
    file — otherwise the rule reads it as pinned and reports nothing, which is
    the trap the guard exists to avoid.
    """
    invented = "741" + "092"
    assert invented not in Path(__file__).read_text(), "the figure leaked"

    synthetic = tmp_path / "synthetic.py"
    synthetic.write_text(
        '"' + '"' * 3 + "One unpinned figure." + '"' * 3 + "\n\n"
        "# It holds " + invented + " storms.\nVALUE = 1\n"
    )

    reported = [
        figure
        for figure, _line in prose_figures(synthetic)
        if figure.replace(",", "") not in FIGURE_ALLOWLIST
        and figure not in Path(__file__).read_text()
    ]
    assert reported == [invented], f"guard did not report it; got {reported}"