"""/comparison refuses more than two cyclones instead of quietly truncating.

The endpoint used to accept any number and answer for the first two, adding a
note: "3 cyclones. Deltas are reported for exactly two, because a delta between
three storms is not a number." The note is honest and the response is still
wrong — a caller asking about five storms gets figures for two, and the only
evidence that three were dropped is prose they have to notice.

This was tolerable while the caches were keyed on `(cyclone_id, scenario_id)`
and each storm cost a full flood computation. The caches are now wind-keyed, so
the cost argument is gone and only the correctness one remains: a delta is
defined between two figures, and three cyclones is not a comparison.

The 400 is deliberate over a 200-with-a-note: a refusal tells the caller their
request was wrong, which they can act on; a truncation tells them nothing they
would read.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

REMAL = "2024145N14087"
A = "1970324N05143"
B = "1970326N10071"
C = "1971124N10093"


def _ids(*sids: str) -> str:
    return ",".join(sids)


def test_three_cyclones_is_a_400_naming_the_limit() -> None:
    response = client.get("/comparison", params={"category": 6, "cyclone_ids": _ids(REMAL, A, B)})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "at most 2" in detail, detail
    assert "3" in detail, "the error must say how many were sent"


def test_the_error_suggests_a_pair_the_caller_can_actually_run() -> None:
    """The refusal is only useful if it hands back a runnable request."""
    detail = client.get(
        "/comparison", params={"category": 6, "cyclone_ids": _ids(REMAL, A, B)}
    ).json()["detail"]
    assert f"cyclone_ids={REMAL},{A}" in detail, detail


def test_two_cyclones_still_compares() -> None:
    """The other half: the limit must not have narrowed the working case."""
    response = client.get("/comparison", params={"category": 6, "cyclone_ids": _ids(REMAL, A)})
    assert response.status_code == 200
    body = response.json()
    assert [c["cyclone_id"] for c in body["cyclones"]] == [REMAL, A]
    assert body["deltas"]["between"] == [REMAL, A]
    assert "surge_m_delta" in body["deltas"]


def test_one_cyclone_still_answers_with_its_no_delta_note() -> None:
    response = client.get("/comparison", params={"category": 6, "cyclone_ids": _ids(REMAL)})
    assert response.status_code == 200
    deltas = response.json()["deltas"]
    assert deltas["between"] == [REMAL]
    assert "nothing to compare it against" in deltas["note"]


def test_five_cyclones_is_also_refused() -> None:
    response = client.get(
        "/comparison", params={"category": 6, "cyclone_ids": _ids(REMAL, A, B, C, REMAL)}
    )
    assert response.status_code == 400
    assert "at most 2" in response.json()["detail"]


def test_the_unknown_id_check_still_runs_for_a_valid_pair() -> None:
    response = client.get("/comparison", params={"category": 6, "cyclone_ids": _ids(REMAL, "nope")})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_no_three_cyclone_note_is_left_in_the_source() -> None:
    """The truncation branch is gone; a leftover note would be dead prose.

    Not worth a test on its own merits — it is here because removing the
    behaviour without removing its explanation leaves the file claiming a
    contract it no longer honours.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "backend" / "main.py").read_text()
    assert "Deltas are reported for exactly two" not in source, (
        "the multi-cyclone note survives in the source; /comparison now rejects "
        "more than two rather than describing the truncation"
    )


@pytest.mark.parametrize("sids", [(REMAL, A, B), (REMAL, A, B, C)])
def test_refused_requests_never_compute_a_flood(sids) -> None:
    """The refusal has to come before the work, not after it.

    Ordering is the whole point of a 400 here: validating ids is cheap, and a
    caller who sends five storms should not pay five floods to be told no.
    """
    from backend import main as main_module

    calls: list[float] = []
    real = main_module.run_flood_model

    def counting(wind):
        calls.append(wind)
        return real(wind)

    main_module.run_flood_model = counting
    try:
        response = client.get(
            "/comparison", params={"category": 6, "cyclone_ids": _ids(*sids)}
        )
    finally:
        main_module.run_flood_model = real
    assert response.status_code == 400
    assert calls == [], "a refused comparison must not run the flood model"
