"""The case-study chip must reach the simulation as `observed`, not as itself.

## What this file is about

The app's case-study chip is keyed `remal_observed`; the backend registers that
scenario as `observed`. The four request sites that carry a `scenario_id` were
handed the chip id verbatim, so:

    GET  /exposure?category=3&scenario_id=remal_observed  -> 400
    POST /risk-analyst {"scenario_id": "remal_observed"}    -> 400

`/exposure` then rendered the 400 as *"Nothing is exposed at this strength"* — a
claim about a request that never ran. The mapping was fixed in the frontend's
request builders (`mobile/api.ts`, `mobile/apiCyclones.ts`), and
`mobile/tests/chipScenarioWire.test.mjs` pins that half.

**These tests pin the other half, and they exist because a frontend-only test
cannot.** A mapping is only correct if the thing it maps *to* is real. So below:
`observed` is proved to resolve for the case study through both endpoints, and
the chip id is proved to still be rejected here — which is what makes the fix a
translation rather than a loosened validation.

## What "unrelated combinations stay isolated" means here

Not "different storms produce different numbers". At a band scenario they do
not — the band wind is a shared IMD midpoint, so cat6 is cat6 for every storm.
Isolation is about *provenance and addressing*: every response names the exact
`(cyclone_id, scenario_id)` it was computed for, and a storm that has no
observed peak is refused rather than handed the case study's.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.ai.advisory import RiskAnalysis
from backend.cyclones.scenarios import DEFAULT_CYCLONE_ID
from backend.main import app
from backend.simulation.surge import surge_for_wind

client = TestClient(app)

REMAL = DEFAULT_CYCLONE_ID  # 2024145N14087 — the case study
# A 1973 Bay of Bengal storm from the committed catalogue, used as the "some
# other storm" side of every isolation assertion below.
OTHER = "1970324N05143"

#: The chip id the UI holds. The backend must never accept it as a scenario.
CHIP_ID = "remal_observed"

#: What the backend calls the same thing.
WIRE_ID = "observed"


def fake_analysis(**kwargs) -> RiskAnalysis:
    """A stand-in for the Gemini call, shaped like a real return value.

    Never a `Mock`: the endpoint `model_dump()`s the return value, and a Mock
    would satisfy any shape while proving nothing about the prompt.
    """
    return RiskAnalysis(
        summary="The surge reaches the substations east of the estuary.",
        findings=[],
        comparison_note="n/a",
        disclaimer="Not an official warning.",
    )


@pytest.fixture
def stub_gemini(monkeypatch):
    """Replace the one quota-spending call, and give the endpoint a key."""
    from backend import main

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(main, "generate_risk_analysis", fake_analysis)
    return main


# ---------------------------------------------------------------------------
# The mapping's target is real: `observed` resolves for the case study
# ---------------------------------------------------------------------------


def test_exposure_serves_the_case_study_at_its_observed_peak() -> None:
    """The request the case-study chip makes, once mapped. Not a band."""
    response = client.get(
        "/exposure", params={"category": 6, "cyclone_id": REMAL, "scenario_id": WIRE_ID}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["scenario_id"] == WIRE_ID
    assert body["scenario_kind"] == WIRE_ID
    # The whole point of `observed`: Remal's own peak, not a category midpoint.
    assert body["wind_is_band_midpoint"] is False
    assert body["cyclone_id"] == REMAL

    # 111.1 km/h is Remal's catalogued peak (60 kt), against cat6's 222.0.
    assert body["wind_kmph"] == pytest.approx(111.1, abs=0.05)
    assert body["surge_m"] == pytest.approx(surge_for_wind(111.1), abs=0.005)


def test_observed_and_a_band_are_different_numbers_for_the_same_storm() -> None:
    """If these were equal, the chip would be a label over the same answer."""
    observed = client.get(
        "/exposure", params={"category": 6, "cyclone_id": REMAL, "scenario_id": WIRE_ID}
    ).json()
    band = client.get(
        "/exposure", params={"category": 6, "cyclone_id": REMAL, "scenario_id": "cat6"}
    ).json()

    assert observed["wind_kmph"] != band["wind_kmph"]
    assert observed["surge_m"] < band["surge_m"]
    assert band["scenario_kind"] == "category"


def test_risk_analyst_accepts_the_observed_scenario_for_the_case_study(
    stub_gemini,
) -> None:
    """The endpoint that rejected the chip outright, with the wire id."""
    response = client.post(
        "/risk-analyst",
        json={"category": 6, "cyclone_id": REMAL, "scenario_id": WIRE_ID},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario_id"] == WIRE_ID
    assert body["cyclone_id"] == REMAL


# ---------------------------------------------------------------------------
# The fix is a translation, not a loosened validation
# ---------------------------------------------------------------------------


def test_the_chip_id_is_still_an_unknown_scenario_here() -> None:
    """The backend is right to refuse it; the client is wrong to send it.

    If this ever returns 200, the fix has been replaced by widening the
    registry to accept a UI token as an alias — which would put the chip's
    vocabulary in the backend's public API forever.
    """
    response = client.get(
        "/exposure", params={"category": 3, "cyclone_id": REMAL, "scenario_id": CHIP_ID}
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert CHIP_ID in detail
    # A refusal that names the alternatives is the useful kind.
    assert WIRE_ID in detail


def test_risk_analyst_still_refuses_the_chip_id_by_name(stub_gemini) -> None:
    """The 400 the review reported, pinned so the symptom stays named.

    `/risk-analyst` validates `scenario_id` before it looks at anything else, so
    the chip id produced a bare 400 with no request body ever examined.
    """
    response = client.post(
        "/risk-analyst",
        json={"category": 6, "cyclone_id": REMAL, "scenario_id": CHIP_ID},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert f"unknown scenario {CHIP_ID!r}" in detail


@pytest.mark.parametrize(
    "junk",
    ["Observed", "REMAL_OBSERVED", "cat9", "cat-3", "observed "],
)
def test_near_miss_spellings_are_refused_rather_than_guessed(junk: str) -> None:
    """The client-side mapping is exact; the server is stricter still."""
    response = client.get(
        "/exposure", params={"category": 6, "cyclone_id": REMAL, "scenario_id": junk}
    )
    assert response.status_code in (400, 422), response.text
    assert "remal_observed" not in response.text


# ---------------------------------------------------------------------------
# Isolation: the pair, not either half
# ---------------------------------------------------------------------------


def test_every_response_names_the_pair_it_was_computed_for() -> None:
    """The cache is keyed on `(cyclone_id, scenario_id)`, so both travel."""
    body = client.get(
        "/exposure", params={"category": 6, "cyclone_id": OTHER, "scenario_id": "cat5"}
    ).json()
    assert (body["cyclone_id"], body["scenario_id"]) == (OTHER, "cat5")
    assert body["cyclone"]["cyclone_id"] == OTHER


def test_a_band_is_the_same_band_for_every_storm_and_says_so() -> None:
    """Isolation is not "different numbers" — at a band they are the same.

    Two storms at cat6 resolve to one shared IMD midpoint. What must differ is
    the addressing, and the numbers must not be quietly presented as
    storm-specific when they are not.
    """
    a = client.get(
        "/exposure", params={"category": 6, "cyclone_id": REMAL, "scenario_id": "cat6"}
    ).json()
    b = client.get(
        "/exposure", params={"category": 6, "cyclone_id": OTHER, "scenario_id": "cat6"}
    ).json()

    assert a["wind_kmph"] == b["wind_kmph"] == pytest.approx(222.0, abs=0.05)
    assert (a["cyclone_id"], b["cyclone_id"]) == (REMAL, OTHER)
    # `wind_is_band_midpoint` is False for cat6, and that is the honest answer:
    # IMD documents no upper bound for the top band, so its 222 km/h is the
    # weakest qualifying wind rather than a midpoint. Asserted here because a
    # reader would reasonably expect the flag to be True at a category band.
    assert a["wind_is_band_midpoint"] is False


def test_a_mid_band_scenario_is_marked_as_a_band_midpoint() -> None:
    """The flag is not uniformly False — that would make it a dead field."""
    body = client.get(
        "/exposure", params={"category": 5, "cyclone_id": REMAL, "scenario_id": "cat5"}
    ).json()
    assert body["wind_is_band_midpoint"] is True
    # `kind` is the three-way distinction: `observed`, `band_midpoint` for a
    # band with a documented upper bound, `category` for the top band that has
    # none. cat6 reports `category` above for exactly this reason.
    assert body["scenario_kind"] == "band_midpoint"


def test_observed_is_refused_for_a_storm_with_no_reported_peak() -> None:
    """`1970324N05143` has no observed scenario. It must not borrow Remal's.

    This is the failure the scenario module exists to prevent: a storm with no
    observed wind silently answering with the case study's would put a real
    historical cyclone's peak on another storm's screen.
    """
    response = client.get(
        "/exposure", params={"category": 6, "cyclone_id": OTHER, "scenario_id": WIRE_ID}
    )
    assert response.status_code == 400, response.text
    assert "unknown scenario" in response.json()["detail"]
    assert REMAL not in response.text


def test_an_unknown_cyclone_is_refused_rather_than_replaced_by_remal() -> None:
    """Absent means Remal. Present-but-unknown is an error, never a substitution."""
    response = client.get(
        "/exposure", params={"category": 6, "cyclone_id": "nonexistent"}
    )
    assert response.status_code == 400
    assert "unknown cyclone" in response.json()["detail"]


def test_the_case_study_payload_is_unchanged_by_any_of_this() -> None:
    """The unscoped request — what the app makes before anything is selected —
    must still be the case study at the requested category."""
    body = client.get("/exposure", params={"category": 6}).json()
    assert body["cyclone_id"] == REMAL
    assert body["scenario_id"] == "cat6"
    # The numbers this project pins in CLAUDE.md. Nested under `count`, and
    # `roads_cut_off` is a count of cut roads, not a connectivity measure.
    assert body["hospitals"]["count"] == 12
    assert body["substations"]["count"] == 22
    assert body["roads_cut_off"]["count"] == 251
    assert body["final_land_area_km2"] == pytest.approx(2680.22, abs=0.01)
    assert body["surge_m"] == pytest.approx(4.4719, abs=0.0001)