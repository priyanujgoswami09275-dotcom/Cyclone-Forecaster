"""The ML gate's verdict must follow the artefact, not a pinned sentence.

The model lost to a flat median — that is the real outcome — and both places
that say so must say it *because the artefact says so*, not because prose
hardcoded "the gate failed". When a real training table ever makes the gate
pass, both strings must change with it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.ai.advisory import build_risk_prompt
from backend.cyclones.scenarios import ScenarioContext
from backend.ml.storm_peak_intensity import SOURCE_MODEL, StormPeakEstimate
from backend.main import app

client = TestClient(app)
CTX = ScenarioContext(cyclone_id="2024145N14087", scenario_id="cat6")


def _est(**overrides) -> StormPeakEstimate:
    base = dict(
        estimate_kt=80.0,
        estimate_source="median_baseline",
        model_kt=95.0,
        baseline_kt=88.0,
        interval_kt=(70.0, 90.0),
        n_training=300,
        beats_baseline=False,
        limitation="x",
    )
    base.update(overrides)
    return StormPeakEstimate(**base)


@pytest.fixture(scope="module")
def exposure() -> dict:
    return client.get("/exposure", params={"category": 6}).json()


@pytest.fixture(scope="module")
def comparison() -> dict:
    return client.get(
        "/comparison",
        params={"category": 6, "cyclone_ids": "2024145N14087,1970324N05143"},
    ).json()


def _prompt(exposure: dict, comparison: dict, est: StormPeakEstimate) -> str:
    return build_risk_prompt(
        context=CTX, exposure=exposure, comparison=comparison, peak_estimate=est
    )


def test_prompt_verbatim_for_the_failed_gate(exposure, comparison):
    prompt = _prompt(exposure, comparison, _est(beats_baseline=False, estimate_source="median_baseline"))
    assert "The model did NOT beat the flat median baseline" in prompt
    assert "median_baseline" in prompt


def test_prompt_follows_the_artefact_when_the_gate_passes(exposure, comparison):
    prompt = _prompt(exposure, comparison, _est(beats_baseline=True, estimate_source=SOURCE_MODEL))
    assert "did NOT beat" not in prompt
    assert "did not beat" not in prompt.lower()
    assert "model" in prompt


def test_ml_figure_disclosure_verbatim_when_the_gate_failed():
    from backend.main import _ml_figure_disclosure  # noqa: F401

    s = _ml_figure_disclosure(_est(beats_baseline=False))
    assert "did not beat its own baseline gate" in s


def test_ml_figure_disclosure_follows_the_artefact_when_the_gate_passes():
    from backend.main import _ml_figure_disclosure  # noqa: F401

    s = _ml_figure_disclosure(_est(beats_baseline=True, estimate_source=SOURCE_MODEL))
    assert "did not beat" not in s
    assert "flat-median baseline" not in s
