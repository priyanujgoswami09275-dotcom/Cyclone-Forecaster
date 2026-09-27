"""Tests for the FastAPI layer (Module C).

The simulation layer is already covered by test_flood / test_dem_and_surge /
test_module_b. What needs testing HERE is the wiring and the promises the API
makes to a client:

- `category` is an IMD band index 0-6, mapped to a wind, and anything
  outside that range is a 422 rather than a silent default;
- every response carries the honesty metadata its numbers require, because a
  client that drops a field is making a claim the backend refused to make;
- the honesty metadata is not merely present but says the right thing — the
  shelter block must not claim real data, the allocation must not report
  feasibility it did not achieve;
- `/routes` and `/allocation` agree about which shelters exist, since they
  are computed from the same cached set;
- an unreachable route says *why*, and does not blame a flood that isn't there;
- no handler reaches the network.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.locations import all_localities, localities, scoping


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


class TestCategoryMapping:
    def test_categories_are_the_seven_imd_bands(self, client):
        response = client.get("/categories")
        assert response.status_code == 200
        categories = response.json()["categories"]
        assert len(categories) == 7
        # Weakest to strongest, matching the slider's direction in PRD.md.
        assert categories[0]["imd_category"] == "Depression"
        assert categories[-1]["imd_category"] == "Super Cyclonic Storm"

    def test_band_wind_increases_with_category(self, client):
        winds = [c["wind_kmph"] for c in client.get("/categories").json()["categories"]]
        assert winds == sorted(winds), "higher category must not mean weaker wind"

    def test_representative_wind_lies_inside_its_band(self, client):
        for category in client.get("/categories").json()["categories"]:
            band, wind = category["band_kmph"], category["wind_kmph"]
            assert band["lower"] <= wind <= band["upper"], category

    def test_out_of_range_category_is_422(self, client):
        for bad in (-1, 7, 99):
            for path in ("/surge-zone", "/exposure", "/allocation"):
                assert client.get(f"{path}?category={bad}").status_code == 422

    def test_non_numeric_category_is_422(self, client):
        assert client.get("/exposure?category=severe").status_code == 422

    def test_dead_zone_is_reported_not_hidden(self, client):
        """Categories the model cannot resolve must say so, not fake a surge."""
        categories = client.get("/categories").json()["categories"]
        weak = [c for c in categories if c["surge_m"] == 0]
        assert weak, "the documented dead zone should still exist"
        for category in weak:
            assert category["note"], f"category {category['category']} is silent about it"


class TestSurgeZone:
    @pytest.fixture(scope="class")
    @classmethod
    def low(self, client):
        return client.get("/surge-zone?category=0").json()

    @pytest.fixture(scope="class")
    @classmethod
    def high(self, client):
        return client.get("/surge-zone?category=6").json()

    def test_carries_both_area_figures_and_the_estimate_flag(self, high):
        for key in ("final_land_area_km2", "drawn_area_km2", "is_estimate", "area_disclosure"):
            assert key in high, key
        assert high["is_estimate"] is True

    def test_drawn_area_never_exceeds_modelled_area(self, high):
        """Reporting the larger figure as 'flooded' would overstate the impact."""
        assert high["drawn_area_km2"] <= high["final_land_area_km2"] + 0.01

    def test_peak_category_floods_land(self, high):
        assert high["final_land_area_km2"] > 0

    def test_dead_zone_category_floods_nothing(self, low):
        assert low["final_land_area_km2"] == 0

    def test_geojson_is_a_feature_collection_with_the_timeline(self, high):
        geojson = high["geojson"]
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == high["frame_count"] == 10

    def test_surge_provenance_travels_with_the_polygon(self, high):
        surge = high["surge"]
        assert surge["is_estimate"] is True
        assert "loo_mae_m" in surge
        assert surge["forward_speed_assumed"] is True


class TestExposure:
    @pytest.fixture(scope="class")
    @classmethod
    def high(self, client):
        return client.get("/exposure?category=6").json()

    def test_uses_the_contract_field_names(self, high):
        for key in ("hospitals", "substations", "roads_cut_off"):
            assert key in high, key

    def test_definitions_ship_with_the_numbers(self, high):
        assert "NOT a network connectivity analysis" in high["definitions"]["road_cut_off"]

    def test_counts_match_the_feature_lists(self, high):
        for key in ("hospitals", "substations", "roads_cut_off"):
            assert high[key]["count"] == len(high[key]["features"])

    def test_finds_real_exposed_assets_at_peak(self, high):
        assert high["hospitals"]["count"] > 0
        assert high["substations"]["count"] > 0
        assert high["roads_cut_off"]["count"] > 0

    def test_nothing_is_exposed_without_a_flood(self, client):
        empty = client.get("/exposure?category=0").json()
        assert empty["hospitals"]["count"] == 0
        assert empty["substations"]["count"] == 0
        assert empty["roads_cut_off"]["count"] == 0


class TestRoutes:
    def test_unknown_origin_is_404_with_a_pointer(self, client):
        response = client.get("/routes?category=0&origin=atlantis")
        assert response.status_code == 404
        assert "/localities" in response.json()["detail"]

    def test_missing_origin_is_422(self, client):
        assert client.get("/routes?category=0").status_code == 422

    def test_reachable_route_has_coordinates(self, client):
        route = client.get("/routes?category=6&origin=kakdwip").json()
        assert route["reachable"] is True
        assert len(route["coordinates"]) >= 2
        assert route["length_km"] > 0

    def test_unreachable_is_200_with_a_reason(self, client):
        """'No safe route' is a result to act on, not an HTTP error."""
        route = client.get("/routes?category=0&origin=canning").json()
        assert route["reachable"] is False
        assert route["reason"]
        assert route["coordinates"] == []

    def test_reason_does_not_blame_a_flood_that_is_not_there(self, client):
        """The misleading direction: 'cut off' at zero surge reads as a warning."""
        route = client.get("/routes?category=0&origin=canning").json()
        if not route["reachable"]:
            assert "cut off" not in route["reason"].lower()

    def test_shelter_disclosure_never_claims_real_data(self, client):
        for category in (0, 6):
            route = client.get(f"/routes?category={category}&origin=kakdwip").json()
            assert route["shelter"]["is_demo_data"] is True
            assert route["shelter_status"]["is_demo_data"] is True
            assert "NOT verified" in route["shelter_status"]["disclosure"]


class TestAllocation:
    @pytest.fixture(scope="class")
    @classmethod
    def high(self, client):
        return client.get("/allocation?category=6").json()

    def test_lp_solves_and_assigns_everyone(self, high):
        """The whole point of the shelter-allocation delighter."""
        assert high["unmet_demand"] == 0
        assigned = sum(
            a["people"] for row in high["allocation"] for a in row["assignments"]
        )
        assert assigned == high["capacity_basis"]["estimated_demand_people"] > 0

    def test_no_shelter_exceeds_its_capacity(self, high):
        for load in high["shelter_loads"]:
            assert load["assigned"] <= load["capacity_people"]

    def test_capacity_basis_states_the_derivation(self, high):
        basis = high["capacity_basis"]
        assert basis["shelters_are_real"] is False
        assert "DERIVED" in basis["rule"]
        assert "NOT surveyed" in basis["rule"]

    def test_shelter_status_is_attached(self, high):
        assert high["shelter_status"]["is_demo_data"] is True

    def test_population_method_is_labelled_an_estimate(self, high):
        assert high["population_method"]["is_estimate"] is True

    def test_routes_and_allocation_agree_on_shelters(self, client):
        """Different code paths; they must not drift on the shelter set."""
        allocation = client.get("/allocation?category=6").json()
        route = client.get("/routes?category=6&origin=canning").json()
        allocation_names = {load["shelter"] for load in allocation["shelter_loads"]}
        assert route["shelter"]["name"] in allocation_names
        assert (
            route["capacity_basis"]["total_capacity_people"]
            == allocation["capacity_basis"]["total_capacity_people"]
        )


class TestScoping:
    def test_study_area_excludes_the_kolkata_core(self):
        """The bbox clips northern Kolkata; its density would swamp the delta."""
        names = {loc.name for loc in localities()}
        assert "Kakdwip" in names and "Patharpratima" in names
        assert "Bidhannagar" not in names and "New Town" not in names

    def test_excluded_places_are_disclosed_not_silently_dropped(self):
        info = scoping()
        assert info["localities_excluded"] > 0
        assert "New Town" in info["excluded_names"]
        assert "not the boundary" in info["disclosure"]

    def test_localities_endpoint_carries_the_scoping(self, client):
        payload = client.get("/localities").json()
        assert payload["count"] == len(localities()) < len(all_localities())
        assert "scoping" in payload

    def test_every_case_study_town_is_addressable(self, client):
        ids = {loc["id"] for loc in client.get("/localities").json()["localities"]}
        for expected in ("kakdwip", "patharpratima", "namkhana", "gosaba", "sagar"):
            assert expected in ids, expected


class TestServiceContract:
    def test_health_reports_the_data_it_actually_has(self, client):
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["shelters_verified"] == 0
        assert health["advisory_implemented"] is True
        # "implemented" and "ready" are different: ready tracks the key, which
        # is read from the environment and never echoed back.
        assert isinstance(health["advisory_ready"], bool)
        assert health["advisory_model"] == "gemini-3.8-flash"
        assert "GEMINI_API_KEY" not in json.dumps(health)
        assert health["building_centroids"] > 100_000

    def test_root_lists_the_contract(self, client):
        endpoints = client.get("/").json()["endpoints"]
        for path in ("/surge-zone", "/exposure", "/routes", "/allocation", "/advisory"):
            assert any(path in entry for entry in endpoints), path

    def test_advisory_validates_like_the_other_endpoints(self, client):
        """Same contract as every GET: out-of-range is 422, unknown id is 404."""
        for bad in (-1, 7):
            assert client.post(f"/advisory?category={bad}&origin=kakdwip").status_code == 422
        assert client.post("/advisory?category=6").status_code == 422
        assert client.post("/advisory?category=6&origin=atlantis").status_code == 404

    def test_advisory_without_a_key_is_503_not_invented_copy(self, client, monkeypatch):
        """Module D is wired. Unconfigured must still be a detectable gap.

        A stub returning plausible advisory prose here would be indistinguishable
        from a real answer to the user, which is the one thing this service must
        never do.
        """
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        response = client.post("/advisory?category=6&origin=kakdwip")
        assert response.status_code == 503
        assert "GEMINI_API_KEY" in response.json()["detail"]

    def test_no_handler_calls_a_network_service(self, monkeypatch):
        """Rules.md: Overpass/IBTrACS/GEE are pre-fetch scripts, never runtime.

        POST /advisory is the one deliberate exception — it calls Gemini, and
        only because a human asked for it. It is covered separately in
        test_module_d.py.
        """
        import requests

        def explode(*args, **kwargs):
            raise AssertionError("a request handler attempted a network call")

        monkeypatch.setattr(requests, "get", explode)
        monkeypatch.setattr(requests, "post", explode)
        monkeypatch.setattr(requests.Session, "request", explode)

        client = TestClient(main.app)
        assert client.get("/surge-zone?category=0").status_code == 200
        assert client.get("/exposure?category=0").status_code == 200
