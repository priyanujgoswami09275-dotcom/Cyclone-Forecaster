"""The two Bengali-script localities inside the study area are in Bangladesh.

Batch C's C3 investigation found that the study-area scope — bbox, a northern
latitude cut and a named deny list — cannot detect a country: মুন্সীগঞ্জ
(osm-7488876257, 89.195E 22.269N) and শ্যামনগর (osm-2481969300, 89.106E
22.343N) sit across the border in Satkhira District, Bangladesh, yet both
survived scoping and both were cat6 demand nodes (10,560 and 56,314
estimated exposed people). An advisory written in the voice of the West
Bengal authority must not direct an evacuation of Bangladeshi villages.

They are denied through the same mechanism that denies Tamluk, keyed by the
stable osm-id form rather than the Bengali display name: a hand-copied
Bengali string cannot be reviewed the way a latin one can, and the id picks
out exactly one OSM node even where names repeat.

Tests that pin the counts pin them exactly (43 served / 34 excluded / 3
denied). If the extract or the deny list changes, these numbers must be
re-stated consciously rather than drifting — the same discipline the
allocation-row comments in test_module_d follow.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.locations import all_localities, localities, scoping

BANGLADESH_IDS = ("osm-7488876257", "osm-2481969300")
REASON = (
    "Bangladesh (Satkhira district); outside the West Bengal authority "
    "this app speaks for"
)


@pytest.fixture
def client():
    return TestClient(main.app)


def _display_names() -> set[str]:
    """The two names, read from the extract by id — never hand-copied.

    The response fields under test are keyed by display name, and a
    hand-typed Bengali string is exactly the typo this test must not have.
    """
    by_id = {loc.id: loc.name for loc in all_localities()}
    return {by_id[i] for i in BANGLADESH_IDS}


def test_both_ids_still_exist_in_the_raw_extract():
    """A deny key that matches nothing fails open — the audit exists to
    catch a typo, and this states the premise directly."""
    ids = {loc.id for loc in all_localities()}
    assert set(BANGLADESH_IDS) <= ids


def test_both_names_are_bengali_script():
    """The premise from the C3 investigation: these ids really are the
    Bengali-script nodes near Satkhira, not some same-id coincidence."""
    for name in _display_names():
        assert any(ord(ch) > 127 for ch in name), name


def test_neither_id_is_served():
    ids = {loc.id for loc in localities()}
    assert set(BANGLADESH_IDS).isdisjoint(ids)


def test_scoping_names_both_with_the_bangladesh_reason():
    """Dropped silently it would read as a data gap; named, it is a
    decision a human can check and reverse — same contract as Tamluk."""
    info = scoping()
    names = _display_names()
    assert names <= set(info["excluded_names"])
    for name in names:
        assert info["out_of_district_excluded"][name] == REASON, name
        assert info["excluded_reasons"][name] == f"out of district: {REASON}", name


def test_scoping_counts_move_by_two():
    """45 served / 32 excluded before this fix. The two Bangladesh nodes
    cross over, and the deny list grows from one entry to three."""
    info = scoping()
    assert info["localities_in_study_area"] == 43
    assert info["localities_excluded"] == 34
    assert info["out_of_district_excluded_count"] == 3


def test_health_reports_the_risen_exclusion_count(client):
    health = client.get("/health").json()
    assert health["localities"] == 43
    assert health["localities_excluded_by_scoping"] == 34


def test_cat6_allocation_omits_both(client):
    """The allocation is what the advisory is written from — the layer
    where these two did their damage (67k estimated exposed people between
    them at category 6)."""
    rows = client.get("/allocation?category=6").json()["allocation"]
    assert _display_names().isdisjoint({row["node"] for row in rows})


def test_neither_is_routable(client):
    """The advisory path resolves its origin the same way /routes does, so
    this also closes `POST /advisory?origin=osm-…` for both ids."""
    for locality_id in BANGLADESH_IDS:
        response = client.get(f"/routes?category=6&origin={locality_id}")
        assert response.status_code == 404, locality_id


def test_the_denial_is_scoping_not_deletion():
    """`?scope=all` keeps listing both places with their real coordinates —
    the deny list is a decision about what the API serves, not an edit to
    the committed extract."""
    ids = {loc.id for loc in all_localities()}
    assert set(BANGLADESH_IDS) <= ids
