"""The request path must not read the 27 MB archive it was trained from.

## The failure this file exists for

`/risk-analyst` is the only endpoint whose output *is* a model figure. It calls
`baseline_estimate()` → `trained_model()`, and `trained_model()` used to call
`build_training_set()` → `read_ni_rows()`, which opens

    ibtracs.NI.list.v04r01.csv    # ~27 MB, **not in git**

That file is a required *local* input: it is how `data/cyclones/catalogue.json`
was generated in the first place, and it is deliberately not committed. So on any
clean clone, any CI runner, and any Vercel deploy, `/risk-analyst` returned a
bare `500` — with no error message naming the missing input, because the missing
input is a `FileNotFoundError` several layers below a route that documents no
failure mode. Locally it worked, because the archive happened to be in the
directory.

## How these tests would have caught it

They trap the two functions that open the file and assert the answer is
unaffected. If anyone reintroduces a refit on the request path — directly, or by
adding a "helpful" fallback when the artefact is missing — the trap raises and
the test fails with the caller's intent in the message.

A mocked test cannot make that claim on its own, because the archive was present
in this working directory and every code path "succeeded". That is why the
deployment-safety claim is verified separately by running the endpoint in a
checkout that does not contain the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.ai.advisory import RiskAnalysis
from backend.cyclones.scenarios import DEFAULT_CYCLONE_ID
from backend.main import app
from backend.ml import storm_peak_intensity as ml
from backend.ml.storm_peak_intensity import SOURCE_BASELINE, baseline_estimate

client = TestClient(app)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _trap(*args, **kwargs):  # pragma: no cover - only reached if regressed
    raise AssertionError(
        "the request path tried to read the raw IBTrACS archive, which is not "
        "in git. Fit offline with "
        "`python -m backend.data_pipeline.train_storm_peak_intensity` and read "
        "data/ml/storm_peak_intensity.json at runtime instead."
    )


@pytest.fixture
def no_raw_archive(monkeypatch):
    """Make the raw archive unreachable, then clear anything cached from it.

    `trained_model` is `lru_cache`d, so without the `cache_clear` this fixture
    would pass against a model a *previous* test loaded from the archive — the
    test would be measuring the cache, not the code path.
    """
    monkeypatch.setattr(ml, "build_training_set", _trap)
    monkeypatch.setattr(ml, "read_ni_rows", _trap)
    ml.trained_model.cache_clear()
    try:
        yield
    finally:
        ml.trained_model.cache_clear()


# ---------------------------------------------------------------------------
# The artefact is committed, which is what makes the above possible
# ---------------------------------------------------------------------------


def test_the_artefact_is_committed_not_derived_on_demand() -> None:
    """The whole fix is this sentence: the model is in version control."""
    assert ml.ARTEFACT_PATH.is_file(), f"missing {ml.ARTEFACT_PATH}"
    assert ml.ARTEFACT_PATH == REPO_ROOT / "data" / "ml" / "storm_peak_intensity.json"
    # If it were ever gitignored, `is_file()` would still pass locally and the
    # clean-checkout failure would come straight back.
    tracked = REPO_ROOT / ".git"
    assert tracked.is_dir()
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(ml.ARTEFACT_PATH.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, f"{ml.ARTEFACT_PATH} is not tracked by git"


def test_the_raw_archive_is_the_thing_that_is_not_committed() -> None:
    """Stated as a fact so the asymmetry cannot be reversed by accident."""
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "ibtracs.NI.list.v04r01.csv"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert listed.returncode != 0, (
        "the raw IBTrACS archive is now tracked. That is a deliberate reversal "
        "of the design — 27 MB of upstream data in git to avoid a rebuild — "
        "and should be a decision, not an accident."
    )


# ---------------------------------------------------------------------------
# The claim: the estimate is reachable with the archive unreachable
# ---------------------------------------------------------------------------


def test_the_estimate_needs_no_archive(no_raw_archive: None) -> None:
    estimate = baseline_estimate()
    assert estimate.estimate_kt == 50.0
    assert estimate.estimate_source == SOURCE_BASELINE == "median_baseline"
    assert estimate.is_a_prediction is False
    assert estimate.n_training == 300
    assert estimate.beats_baseline is False


def test_the_gate_still_binds_on_the_artefact(no_raw_archive: None) -> None:
    """Loading the model must not lose the gate that disqualified it.

    The gate is the reason the shipped figure is a constant. A loader that
    restored the coefficients but not the report would either crash or, worse,
    ship `model_kt` as if it had earned the number.
    """
    _, report = ml.trained_model()
    assert report.beats_baseline is False
    assert (report.n, report.mae_kt, report.baseline_mae_kt) == (300, 21.944, 21.027)

    with_features = ml.estimate_for((15.0, 300.0, 80.0, 21.5, 88.0, 5.0, 120.0))
    assert with_features.estimate_source == SOURCE_BASELINE
    assert with_features.estimate_kt == with_features.baseline_kt == 50.0
    # The losing output stays visible as evidence. Suppressing it would hide
    # why the model is not the number on screen.
    assert with_features.model_kt == pytest.approx(64.7308, abs=0.001)
    assert with_features.is_a_prediction is False


def test_the_artefact_matches_what_the_committed_gate_says() -> None:
    """Guard against the JSON and this module disagreeing about the outcome."""
    import json

    payload = json.loads(ml.ARTEFACT_PATH.read_text())
    _, report = ml.trained_model()
    assert payload["report"]["beats_baseline"] == report.beats_baseline is False
    assert payload["median_kt"] == 50.0
    assert payload["feature_names"] == list(ml.FEATURE_NAMES)


def test_a_missing_artefact_raises_instead_of_refitting(monkeypatch) -> None:
    """No fallback. A fallback *is* the defect, in a different costume.

    If a future change makes this quietly rebuild from the archive, the 500
    comes back — and it comes back invisibly, because the rebuild succeeds on any
    machine that happens to have the file.
    """
    monkeypatch.setattr(ml, "ARTEFACT_PATH", REPO_ROOT / "data" / "ml" / "does-not-exist.json")
    monkeypatch.setattr(ml, "build_training_set", _trap)
    ml.trained_model.cache_clear()
    try:
        with pytest.raises(RuntimeError) as caught:
            ml.trained_model()
    finally:
        ml.trained_model.cache_clear()

    message = str(caught.value)
    assert "does-not-exist.json" in message
    assert "train_storm_peak_intensity" in message


def test_an_artefact_written_for_other_features_is_refused() -> None:
    """Reuse across a feature change must fail loudly, not reinterpret."""

    def with_renamed_features(tmp_path: Path) -> Path:
        import json

        payload = json.loads(ml.ARTEFACT_PATH.read_text())
        payload["feature_names"] = ["a_different_feature"]
        path = tmp_path / "wrong_features.json"
        path.write_text(json.dumps(payload))
        return path

    import json as _json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = with_renamed_features(Path(tmp))
        with pytest.raises(ValueError, match="Retrain rather than guess"):
            ml.StormPeakIntensityModel.from_json(path)
        # The sanity check above is that the file itself is otherwise valid.
        assert _json.loads(path.read_text())["median_kt"] == 50.0


def test_an_artefact_with_an_unreadable_report_is_refused(tmp_path: Path) -> None:
    """A report missing `n` must not default to 0 and say "0 storms"."""
    import json

    payload = json.loads(ml.ARTEFACT_PATH.read_text())
    del payload["report"]["n"]
    path = tmp_path / "no_n.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="Retrain rather than read a report"):
        ml.StormPeakIntensityModel.from_json(path)


# ---------------------------------------------------------------------------
# The endpoint, which is where the 500 was
# ---------------------------------------------------------------------------


def fake_analysis(**kwargs) -> RiskAnalysis:
    """Never a `Mock` — the endpoint `model_dump()`s the return value."""
    return RiskAnalysis(
        summary="The surge reaches the substations east of the estuary.",
        findings=[],
        comparison_note="n/a",
        disclaimer="Not an official warning.",
    )


def test_the_risk_analyst_endpoint_answers_without_the_archive(
    no_raw_archive, monkeypatch
) -> None:
    """The 500, end to end, with Gemini stubbed and the archive unreachable.

    Only the model call is stubbed. Everything the endpoint actually computes —
    the scenario, the flood, the exposure, the comparison, and the peak estimate
    this file is about — runs for real.
    """
    from backend import main

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(main, "generate_risk_analysis", fake_analysis)

    response = client.post(
        "/risk-analyst",
        json={"category": 6, "cyclone_id": DEFAULT_CYCLONE_ID, "scenario_id": "cat6"},
    )
    assert response.status_code == 200, response.text

    estimate = response.json()["peak_estimate"]
    assert estimate["estimate_kt"] == 50.0
    assert estimate["estimate_source"] == "median_baseline"
    assert estimate["is_a_prediction"] is False
    assert estimate["model_kt_unused"] is None


# ---------------------------------------------------------------------------
# The offline path is untouched
# ---------------------------------------------------------------------------


def test_the_offline_training_path_still_reads_the_archive() -> None:
    """The dependency moved; it did not disappear.

    `build_training_set` is unchanged and still opens the raw archive, because
    retraining must remain reproducible from upstream data. What changed is that
    no request does it.
    """
    from backend.cyclones import historical

    assert ml.build_training_set.__module__ == ml.__name__
    # The archive it reads is the same one, by the same constant.
    assert historical.DEFAULT_IBTRACS_PATH.name == "ibtracs.NI.list.v04r01.csv"
    assert ml.DEFAULT_IBTRACS_PATH == historical.DEFAULT_IBTRACS_PATH