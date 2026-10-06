"""For a non-band scenario the header's band must describe the wind, not the index.

`?category=3&scenario_id=observed` used to report cat3's band ("Severe
Cyclonic Storm") at the top level while the nested `surge.imd_category`
correctly named the real wind's class — the response disagreed with itself.
`_category_header` now derives the label and bounds from the resolved wind
for observed scenarios. `category` stays the requested index.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.cyclones.registry import registry
from backend.cyclones.scenarios import ScenarioContext, scenarios_for
from backend.main import _category_header, app

client = TestClient(app)

#: 1996 storm trek from the committed catalogue, peak 120.4 kmph.
CYCLONE = "1996288N09092"


def test_observed_header_names_the_winds_band():
    """cat3 requested, but cyclone 1996288N09092's 120.4 kmph is Very Severe."""
    response = client.get(
        "/surge-zone",
        params={"category": 3, "cyclone_id": CYCLONE, "scenario_id": "observed"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == 3
    assert body["wind_kmph"] == pytest.approx(120.4, abs=0.05)
    assert body["imd_category"] == "Very Severe Cyclonic Storm"
    assert body["imd_category"] == body["surge"]["imd_category"]
    assert body["band_kmph"]["lower"] == 118


def test_header_label_matches_surge_label_for_every_scenario():
    record = registry().get(CYCLONE)
    assert record is not None
    for scenario in scenarios_for(record):
        ctx = ScenarioContext(cyclone_id=CYCLONE, scenario_id=scenario.scenario_id)
        header = _category_header(
            3,  # Deliberately the 'wrong' index: the test must pass anyway.
            ctx,
            wind_kmph=ctx.wind_kmph(record),
            is_band_midpoint=scenario.wind_is_band_midpoint,
        )
        assert header["imd_category"] == header["surge"]["imd_category"], (
            f"{scenario.scenario_id}: header {header['imd_category']!r} vs "
            f"surge {header['surge']['imd_category']!r}"
        )
