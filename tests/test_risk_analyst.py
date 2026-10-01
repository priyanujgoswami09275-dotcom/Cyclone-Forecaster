"""The AI Risk Analyst: evidence-grounded, pinned model, no fallback.

The tests that matter are about what the model may *not* do: invent a number,
claim official standing, speak as IMD, or treat the ML estimate as though it
were the surge figure. A risk analyst that sounds authoritative while doing any
of those is worse than no risk analyst, because it lends its voice to the error.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.ai.advisory import (
    ADVISORY_MODEL,
    RISK_SYSTEM_PROMPT,
    RiskAnalysis,
    RiskFinding,
    build_risk_prompt,
)
from backend.cyclones.scenarios import ScenarioContext
from backend.main import app
from backend.ml.storm_peak_intensity import (
    StormPeakEstimate,
    estimate_for,
    trained_model,
)
from backend.simulation.surge import surge_for_wind

REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

#: The case study, at the top band. Every figure in the fixtures below is one the
#: app really computes for this pair.
CTX = ScenarioContext(cyclone_id="2024145N14087", scenario_id="cat6")

#: A real estimate: the gate failed, so this carries the honest fields a prompt
#: has to label correctly.
PEAK_ESTIMATE = estimate_for((15.0, 300.0, 80.0, 21.5, 88.0, 5.0, 120.0))


@pytest.fixture(scope="module")
def exposure() -> dict:
    return client.get("/exposure", params={"category": 6}).json()


@pytest.fixture(scope="module")
def comparison() -> dict:
    return client.get(
        "/comparison",
        params={"category": 6, "cyclone_ids": "2024145N14087,1970324N05143"},
    ).json()


def fake_analysis(**kwargs) -> RiskAnalysis:
    """A stand-in for a real call. Never a `Mock` — the return type is checked
    by the endpoint's own `model_dump()`, and a Mock would satisfy anything."""
    return RiskAnalysis(
        summary="The surge reaches 26 substations.",
        findings=[
            RiskFinding(
                finding="Substations in the flood zone",
                evidence="26 substations exposed",
                severity="HIGH",
                evidence_kind="computed",
            )
        ],
        comparison_note="n/a",
        disclaimer="Not an official warning.",
    )


def _prompt(exposure: dict, comparison: dict, advisory: dict | None = None) -> str:
    return build_risk_prompt(
        context=CTX,
        exposure=exposure,
        comparison=comparison,
        peak_estimate=PEAK_ESTIMATE,
        advisory=advisory,
    )


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------


def test_every_finding_must_cite_evidence() -> None:
    """A finding without evidence is an opinion wearing the app's voice."""
    finding = RiskFinding(
        finding="Substations cut off",
        evidence="12 substations exposed district-wide",
        severity="HIGH",
        evidence_kind="computed",
    )
    assert finding.evidence
    assert finding.evidence_kind in (
        "computed",
        "historical",
        "model_estimate",
        "general_knowledge",
    )


def test_severity_is_closed_at_the_type_level() -> None:
    """A severity outside the four is a type error, not a string that ships."""
    with pytest.raises(ValidationError):
        RiskFinding(
            finding="x",
            evidence="y",
            severity="IMMEDIATE",  # the exact word the advisory forbids
            evidence_kind="computed",
        )


def test_evidence_kind_is_closed_at_the_type_level() -> None:
    with pytest.raises(ValidationError):
        RiskFinding(
            finding="x",
            evidence="y",
            severity="HIGH",
            evidence_kind="measured",  # not one of the four
        )


def test_an_analysis_requires_all_four_fields() -> None:
    with pytest.raises(ValidationError):
        RiskAnalysis(summary="only a summary")
    # And the full shape is constructible from real fixtures.
    analysis = RiskAnalysis(
        summary="A summary",
        findings=[],
        comparison_note="Remal against RUTH",
        disclaimer="Not an official warning",
    )
    assert analysis.findings == []


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_the_prompt_is_separated_into_labelled_blocks(exposure, comparison) -> None:
    """Four blocks, each a different kind of claim, so none can borrow another's
    authority: what the app computed, what the ML layer estimated, what the
    comparison found, and what history says."""
    prompt = _prompt(exposure, comparison)
    for block in (
        "COMPUTED FIGURES",
        "MODEL ESTIMATE",
        "COMPARISON",
        "HISTORICAL CONTEXT",
    ):
        assert block in prompt, f"no {block} block"


def test_the_prompt_labels_the_ml_estimate_separately_from_the_surge(
    exposure, comparison
) -> None:
    prompt = _prompt(exposure, comparison)
    assert "1.2" in prompt or "anchored quadratic" in prompt
    assert "authoritative" in prompt

    block = prompt[prompt.index("MODEL ESTIMATE") : prompt.index("MODEL ESTIMATE") + 700]
    assert "ML" in block
    assert "not" in block.lower()
    # The gate's own verdict travels with the figure, so a reader cannot read
    # the number without reading that it lost to a flat median.
    assert "median" in block
    assert "not a prediction" in block.lower()
    # And the ML figure is never the surge figure.
    assert "not the surge" in block.lower()


def test_the_prompt_carries_the_gate_verdict_verbatim(exposure, comparison) -> None:
    """`beats_baseline: False` is in the prompt, not paraphrased away."""
    prompt = _prompt(exposure, comparison)
    block = prompt[prompt.index("MODEL ESTIMATE") : prompt.index("COMPARISON")]
    assert "False" in block or "false" in block
    assert str(PEAK_ESTIMATE.estimate_kt) in block
    assert str(PEAK_ESTIMATE.n_training) in block


def test_the_prompt_forbids_inventing_numbers(exposure, comparison) -> None:
    prompt = _prompt(exposure, comparison)
    lowered = prompt.lower()
    assert "only the numbers" in lowered or "do not" in lowered
    assert "invent" in lowered or "never invent" in lowered


def test_the_prompt_requires_the_disclaimer_and_no_official_claim(
    exposure, comparison
) -> None:
    prompt = _prompt(exposure, comparison)
    lowered = prompt.lower()
    assert "not an official warning" in lowered
    assert "IMD" in prompt
    assert "do not speak as" in lowered or "you are not" in lowered


def test_the_system_prompt_bans_official_claiming() -> None:
    lowered = RISK_SYSTEM_PROMPT.lower()
    assert "not an official warning" in lowered
    assert "imd" in lowered
    assert "invent" in lowered
    assert "authoritative" in lowered


def test_the_prompt_states_the_surge_method(exposure, comparison) -> None:
    """The deterministic law is named, so the analyst cannot present the surge
    as a model output or a forecast."""
    prompt = _prompt(exposure, comparison)
    assert "1.2 x (wind/115)^2" in prompt


def test_the_computed_block_holds_only_real_figures(exposure, comparison) -> None:
    """Every number in the COMPUTED block is one the app actually computed.

    Checked by matching the block against the real responses it was built from —
    a figure in the block that appears in neither payload was invented by the
    prompt, which is the exact failure this test exists for.
    """
    prompt = _prompt(exposure, comparison)
    block = prompt[prompt.index("COMPUTED FIGURES") : prompt.index("MODEL ESTIMATE")]

    assert str(exposure["hospitals"]["count"]) in block
    assert str(exposure["substations"]["count"]) in block
    assert str(exposure["roads_cut_off"]["count"]) in block
    assert f"{exposure['surge']['surge_m']:.2f}" in block
    assert str(exposure["final_land_area_km2"]) in block
    assert str(exposure["wind_kmph"]) in block

    # A third storm's id must not appear: nothing about it was computed.
    assert "1971124N10093" not in block


def test_the_comparison_block_names_the_pair_it_compared(exposure, comparison) -> None:
    prompt = _prompt(exposure, comparison)
    block = prompt[prompt.index("COMPARISON") : prompt.index("HISTORICAL CONTEXT")]
    for cyclone_id in ("2024145N14087", "1970324N05143"):
        assert cyclone_id in block
    # Both entries' surge figures, so the deltas are checkable against their inputs.
    for entry in comparison["cyclones"]:
        assert f"{entry['surge_m']:.4f}" in block


def test_the_historical_block_is_the_same_verified_pool_the_advisory_uses(
    exposure, comparison
) -> None:
    """No new historical facts: the risk analyst uses the pool `/advisory` already
    verified, because a second pool is a second place for an invented storm."""
    from backend.ai.advisory import VERIFIED_HISTORICAL_POOL

    prompt = _prompt(exposure, comparison)
    block = prompt[prompt.index("HISTORICAL CONTEXT") :]
    assert "Amphan" in block
    assert "Yaas" in block
    assert "Bulbul" in block
    # And it is the same pool, not a paraphrase with room to drift.
    assert VERIFIED_HISTORICAL_POOL in prompt


def test_an_advisory_is_optional_and_adds_a_block_when_present(
    exposure, comparison
) -> None:
    without = _prompt(exposure, comparison)
    with_advisory = _prompt(
        exposure, comparison, advisory={"executive_summary": "Evacuate Kakdwip."}
    )
    assert "ADVISORY" not in without or "Evacuate Kakdwip" not in without
    assert "Evacuate Kakdwip" in with_advisory


# --------------------------------------------------------------------------
# The call: pinned model, no fallback
# --------------------------------------------------------------------------


def _function_source(name: str) -> ast.FunctionDef:
    """The node, so a test can read a function's own source range instead of a
    character window that tests formatting rather than behaviour."""
    src = (REPO_ROOT / "backend" / "ai" / "advisory.py").read_text()
    return next(
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == name
    )


def test_the_model_string_is_pinned_with_no_fallback() -> None:
    """One model literal in the whole module, and neither generator can swap it.

    A fallback has to name the model it falls back *to*, so "there is exactly
    one model string in this file" is a stronger statement than "no line of the
    function contains the word". It also survives someone writing a comment
    that says the word `fallback` while explaining there isn't one — which is
    precisely what a window-of-400-characters test would have failed on.
    """
    src = (REPO_ROOT / "backend" / "ai" / "advisory.py").read_text()

    assert f'ADVISORY_MODEL = "{ADVISORY_MODEL}"' in src
    assert ADVISORY_MODEL == "gemini-3.8-flash"

    # Only *code* literals. The module documents the 2026-09-28 3.7 -> 3.8
    # capacity bump in a comment, and that history must stay: a check that
    # grepped prose would force the team to delete the explanation of why the
    # pinned string is what it is. Comments are absent from the AST and
    # docstrings are skipped, so what is left is the strings the code acts on.
    tree = ast.parse(src)
    acted_on: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr):
            continue  # a bare string statement: a docstring, not an operand
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            acted_on.add(node.value)
    found = {
        m for text in acted_on for m in re.findall(r"gemini-[\w.\-]+", text)
    }
    assert found == {ADVISORY_MODEL}, f"a second model literal: {found}"

    # Neither generator contains an `except`, so neither can catch an
    # availability failure and retry against something else. The retry ladder
    # lives in main.py and re-raises; it does not substitute a model.
    for name in ("generate_advisory", "generate_risk_analysis"):
        node = _function_source(name)
        handlers = [n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)]
        assert handlers == [], f"{name} catches and could substitute"


def test_generate_risk_analysis_uses_the_pinned_model() -> None:
    """AST: the body references the constant, the model is not an argument, and
    no other model string appears anywhere in it.

    An arbitrary character window would be testing formatting. Reading the
    function's own source range tests the property: someone pointing this at a
    different model has to touch the constant (reviewable) rather than pass an
    argument at the call site (invisible).
    """
    src = (REPO_ROOT / "backend" / "ai" / "advisory.py").read_text()
    lines = src.splitlines()
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "generate_risk_analysis"
    )
    body = "\n".join(lines[fn.lineno - 1 : fn.end_lineno])

    # Not injectable: there is no way to pass a model in.
    all_args = list(fn.args.args) + list(fn.args.kwonlyargs)
    assert "model" not in {a.arg for a in all_args}, "the model must not be injectable"

    # It reads the pinned constant rather than inlining the string — so the
    # pinned value exists in exactly one place in this module.
    assert "ADVISORY_MODEL" in body
    assert ADVISORY_MODEL not in body, "the model string is inlined here too"

    # And no other model string is hiding in there.
    literals = re.findall(r"gemini-[\w.\-]+", body)
    assert literals == [], f"a second model string: {literals}"


def test_quota_exhaustion_surfaces_the_existing_error_path(
    exposure, comparison, monkeypatch
) -> None:
    """A spent quota raises the SAME error the advisory path raises.

    The backend's taxonomy is `GeminiQuotaError` / `GeminiCapacityError`; the
    mobile client's `describeAdvisoryError` maps those to `quota` / `unavailable`
    kinds. A risk analyst that invented a third path would need a ninth kind,
    which is the thing `advisoryFlow.ts` exists to prevent.
    """
    from backend.ai import advisory
    from backend.main import GeminiQuotaError

    def spent_quota(*args, **kwargs):
        raise GeminiQuotaError("daily limit reached")

    monkeypatch.setattr(advisory, "_generate_content", spent_quota)
    with pytest.raises(GeminiQuotaError):
        advisory.generate_risk_analysis(
            context=CTX,
            exposure=exposure,
            comparison=comparison,
            peak_estimate=PEAK_ESTIMATE,
        )


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


def test_the_endpoint_exists_and_is_a_post(monkeypatch) -> None:
    """Behind a deliberate press: a POST body, never a slider or a chip press.

    `generate_risk_analysis` is stubbed for the same reason `test_module_d`
    stubs `generate_advisory`: an endpoint test that spends a real Gemini call
    is a test that fails when the free tier is exhausted, and the suite would
    then be reporting the weather rather than the code.
    """
    from backend import main

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(main, "generate_risk_analysis", fake_analysis)

    response = client.post("/risk-analyst", json={"category": 6})
    assert response.status_code != 404, "the route does not exist"
    assert response.status_code == 200, response.text


def test_a_missing_api_key_is_the_same_config_error_advisory_already_ships(
    monkeypatch,
) -> None:
    """No key means the analyst cannot run — and it says so the way `/advisory`
    already does: **503** with the exact same disclosure.

    Not a new status and not a new message. A second config path is a second
    place for the two to drift, and a client that already handles the advisory's
    503 would have to learn a second one for the same cause.
    """
    import os

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post("/risk-analyst", json={"category": 6})
    assert response.status_code == 503

    advisory_response = client.post("/advisory?category=6&origin=kakdwip")
    assert advisory_response.status_code == 503, "the reference contract moved"
    assert response.json()["detail"] == advisory_response.json()["detail"]
    assert "GEMINI_API_KEY" in response.json()["detail"]


def test_an_unknown_cyclone_is_a_400_not_a_substitution() -> None:
    response = client.post("/risk-analyst", json={"category": 6, "cyclone_id": "nope"})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_the_endpoint_passes_the_real_payloads(exposure, comparison, monkeypatch) -> None:
    """The analyst is fed the same dicts the client sees, so the prose and the map
    cannot disagree. Verified by intercepting the prompt.

    This is the property the whole endpoint exists for: if it assembled its own
    figures instead of reusing `/exposure` and `/comparison`, the prose and the
    map could drift apart and nothing would notice.
    """
    from backend.ai import advisory

    captured: dict = {}

    def fake_generate(*, prompt, system_instruction=None, config=None, **kwargs):
        captured["prompt"] = prompt
        return RiskAnalysis(
            summary="s",
            findings=[],
            comparison_note="c",
            disclaimer="d",
        )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(advisory, "_generate_content", fake_generate)

    response = client.post(
        "/risk-analyst",
        json={"category": 6, "cyclone_id": "2024145N14087", "scenario_id": "cat6"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis"]["summary"] == "s"
    # Provenance is merged at the top level, like every other response in T7.
    assert body["cyclone_id"] == "2024145N14087"
    assert body["scenario_id"] == "cat6"
    assert body["cyclone"]["name"] == "REMAL"
    assert "prompt" not in body, "the prompt is not leaked to the client"
    assert captured["prompt"], "the model was never called"

    # The figures in the prompt are the ones the client can fetch itself.
    assert str(exposure["hospitals"]["count"]) in captured["prompt"]
    assert str(comparison["deltas"]["between"][0]) in captured["prompt"]


def test_quota_and_capacity_use_advisorys_own_taxonomy(monkeypatch) -> None:
    """The plan's requirement that this endpoint reuse the ladder *verbatim*.

    Before it did, every upstream Gemini failure surfaced as a bare non-JSON
    `500 Internal Server Error` — which tells a client nothing, cannot be told
    apart from a bug in this service, and drops the disclosure `/advisory`
    already ships for the same cause. Verified against the live endpoint rather
    than by reading the source: a spent quota is 429, a busy model is 503 with
    a Retry-After, and anything else is 502.
    """
    from backend import main

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(main, "_sleep", lambda _s: None)  # no real backoff in tests

    def quota(*args, **kwargs):
        raise main.GeminiQuotaError("spent")

    def capacity(*args, **kwargs):
        raise main.GeminiCapacityError("busy")

    def broken(*args, **kwargs):
        raise RuntimeError("kaboom")

    for fn, expected in ((quota, 429), (capacity, 503), (broken, 502)):
        monkeypatch.setattr(main, "generate_risk_analysis", fn)
        response = client.post("/risk-analyst", json={"category": 6})
        assert response.status_code == expected, (expected, response.text)
        # A JSON body a client can parse, not the bare text a 500 produced.
        assert response.headers["content-type"].startswith("application/json")

    # And the 429 carries `/advisory`'s own wording, not a second message that
    # could drift from it.
    monkeypatch.setattr(main, "generate_risk_analysis", quota)
    monkeypatch.setattr(main, "generate_advisory", quota)
    analyst = client.post("/risk-analyst", json={"category": 6})
    advisory = client.post("/advisory?category=6&origin=kakdwip")
    assert analyst.status_code == advisory.status_code == 429
    assert analyst.json()["detail"] == advisory.json()["detail"]


def test_the_capacity_ladder_is_one_shared_implementation() -> None:
    """Both Gemini endpoints must reach the same loop, not a copy of it.

    The extraction is the point: two copies of a retry loop is two places to
    disagree about how many attempts a busy model gets, and only one of them
    would ever be tested. `/advisory` reaches it through
    `_generate_with_capacity_retry`, `/risk-analyst` calls it directly — but
    the loop itself exists exactly once.
    """
    from backend import main

    assert callable(main._with_capacity_retry)
    assert callable(main._generate_with_capacity_retry)

    src = (REPO_ROOT / "backend" / "main.py").read_text()
    # The loop's own counter appears once in the whole module: a second copy
    # would make this 2 and say so.
    assert src.count("for attempt in range(1, CAPACITY_MAX_ATTEMPTS + 1)") == 1
    # And the risk endpoint reaches for it by name.
    assert "_with_capacity_retry(" in src.split("def risk_analyst")[1]


# --------------------------------------------------------------------------
# The analyst cannot change the surge law
# --------------------------------------------------------------------------


def test_the_surge_law_is_untouched_by_the_analyst() -> None:
    assert surge_for_wind(115) == pytest.approx(1.2)
    src = (REPO_ROOT / "backend" / "ai" / "advisory.py").read_text()
    # The AI layer may READ the law's output; it must not import or redefine it.
    assert "def surge_for_wind" not in src
    assert re.search(r"^IMD_BANDS", src, re.M) is None


def test_the_ml_estimate_in_the_payload_says_what_it_is() -> None:
    """The endpoint must surface the ML estimate's own disclosure, not strip it."""
    payload = PEAK_ESTIMATE.to_dict()
    assert payload["is_a_prediction"] is False
    assert payload["beats_baseline"] is False
    assert "NOT a prediction" in payload["limitation"]


def test_the_estimate_is_the_real_one_not_a_test_double() -> None:
    """`PEAK_ESTIMATE` is a real `estimate_for` result, so the prompt tests run
    against the figure the endpoint would really send."""
    assert isinstance(PEAK_ESTIMATE, StormPeakEstimate)
    model, report = trained_model()
    assert PEAK_ESTIMATE.beats_baseline is report.beats_baseline
    assert PEAK_ESTIMATE.n_training == report.n


# --------------------------------------------------------------------------
# The estimate the endpoint sends — and the origin path
# --------------------------------------------------------------------------


def test_baseline_estimate_equals_estimate_for_on_every_shipped_field() -> None:
    """The equivalence `baseline_estimate`'s docstring claims, proved here.

    The docstring asserts that this equality is tested rather than asking a
    reader to take it on trust. This is that test: if the two ever diverge, the
    claim and the assertion fail in the same run instead of the claim quietly
    outliving the fact it describes — which is how a docstring comes to
    describe a system that no longer exists.
    """
    from backend.ml.storm_peak_intensity import SOURCE_BASELINE, baseline_estimate

    shipped = baseline_estimate()
    with_features = estimate_for((15.0, 300.0, 80.0, 21.5, 88.0, 5.0, 120.0))

    for field in (
        "estimate_kt",
        "estimate_source",
        "baseline_kt",
        "interval_kt",
        "n_training",
        "beats_baseline",
        "limitation",
        "is_a_prediction",
    ):
        assert getattr(shipped, field) == getattr(with_features, field), field

    # The one field that differs, and it differs precisely because no track was
    # observed: `estimate_for` has a feature vector, `baseline_estimate` does not.
    assert shipped.model_kt is None
    assert with_features.model_kt is not None

    # And the shipped figure really is the constant — no feature can move it,
    # which is what makes "we did not invent features" a checkable claim.
    assert shipped.estimate_kt == shipped.baseline_kt
    assert shipped.estimate_source == SOURCE_BASELINE == "median_baseline"
    assert shipped.is_a_prediction is False


def test_the_endpoint_sends_a_baseline_not_a_synthesised_feature_vector(
    exposure, comparison, monkeypatch
) -> None:
    """No track was supplied, so no track may be guessed at.

    The request names a cyclone and a scenario. The seven features describe a
    *track* — translation speed, bearing, distance to land, latitude, longitude,
    month, basin distance — and none of them is a property of "category 6
    applied to Remal". A plausible-looking tuple would have produced a `model_kt`
    derived from data nobody observed; the only reason it would not have reached
    a reader is a label.
    """
    from backend import main
    from backend.ml.storm_peak_intensity import baseline_estimate

    captured: dict = {}

    def capture(*, peak_estimate, **kwargs):
        captured["peak"] = peak_estimate
        return fake_analysis()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(main, "generate_risk_analysis", capture)

    response = client.post(
        "/risk-analyst",
        json={"category": 6, "cyclone_id": "2024145N14087", "scenario_id": "cat6"},
    )
    assert response.status_code == 200, response.text

    peak = captured["peak"]
    assert peak == baseline_estimate()
    # `model_kt is None` is the signature of "no features were supplied"; a
    # synthesised vector would have yielded a model output here.
    assert peak.model_kt is None
    assert peak.is_a_prediction is False

    # And the payload the client receives says the same thing.
    estimate = response.json()["peak_estimate"]
    assert estimate["model_kt_unused"] is None
    assert estimate["is_a_prediction"] is False
    assert estimate["estimate_source"] == "median_baseline"


def test_the_origin_path_supplies_real_route_facts(monkeypatch) -> None:
    """`origin_facts` must hold facts, not a dict of silent `None`s.

    `_origin_facts` returns the assigned shelter as an *object* under the key
    `shelter` — `_origin_context` reads it as `facts["shelter"].name`. Reading
    `shelter_name` instead yields `None` for every request, so the prompt would
    tell the model the shelter is unknown. That is a fabricated absence, and it
    is the same failure as a fabricated figure: the model is then free to name
    one, because the block that should have constrained it came back empty.

    This test exists because the bug was real: the endpoint shipped reading
    `facts.get("shelter_name")` and no test passed an `origin` at all.
    """
    from backend import main

    captured: dict = {}

    def capture(*, origin_facts, **kwargs):
        captured["origin_facts"] = origin_facts
        return fake_analysis()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(main, "generate_risk_analysis", capture)

    response = client.post("/risk-analyst", json={"category": 6, "origin": "kakdwip"})
    assert response.status_code == 200, response.text

    facts = captured["origin_facts"]
    assert facts is not None, "an origin was given but no facts were built"
    assert facts["locality_id"] == "kakdwip"
    assert facts["locality_name"], "the locality name is missing"
    assert isinstance(facts["shelter"], str) and facts["shelter"], (
        f"shelter is {facts['shelter']!r} — the key or the type is wrong"
    )
    assert isinstance(facts["route_reachable"], bool)
    # Reachable routes carry a length; an unreachable one carries None rather
    # than a made-up distance.
    if facts["route_reachable"]:
        assert facts["route_length_km"] is not None
        assert facts["route_length_km"] > 0
    else:
        assert facts["route_length_km"] is None or facts["route_length_km"] > 0