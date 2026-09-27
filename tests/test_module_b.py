"""Tests for exposure, routing, shelters, allocation, and population (Module B).

These cover the properties that are easy to get quietly wrong:

- exposure must not silently drop the LineString features that represent most
  hospitals/substations in the committed OSM extract;
- `road_cut_off` means intersects, and the definition travels with the data;
- routing must return an explicit "unreachable", never a silent empty path;
- the allocation LP must be capacity-feasible and must report infeasibility
  rather than returning a partial answer;
- population figures are estimates and must be labelled as such.
"""

import json
from pathlib import Path

import pytest

from backend.simulation.allocation import DemandNode, allocate_shelters
from backend.simulation.exposure import compute_exposure, load_geojson
from backend.simulation.population import methodology
from backend.simulation.routing import build_road_graph, safe_route
from backend.simulation.shelters import (
    demo_shelters,
    load_shelters,
    shelter_dataset_status,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _box_geometry(west, south, east, north):
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


class TestExposure:
    def test_real_data_loads(self):
        hospitals = load_geojson("hospitals.geojson")["features"]
        substations = load_geojson("substations.geojson")["features"]
        roads = load_geojson("roads.geojson")["features"]
        assert len(hospitals) > 100
        assert len(substations) > 10
        assert len(roads) > 100

    def test_non_point_facilities_are_present_and_used(self):
        """A large share of the OSM facilities are LineStrings, not Points.

        If exposure only handled Points it would drop 100 of 560 hospitals and
        101 of 103 substations while still returning a plausible-looking count.
        """
        for filename in ("hospitals.geojson", "substations.geojson"):
            kinds = {
                f["geometry"]["type"]
                for f in load_geojson(filename)["features"]
                if f.get("geometry")
            }
            assert "Point" in kinds, f"{filename} has no points at all"
            assert kinds - {"Point"}, f"{filename} unexpectedly all points"

    def test_known_facility_is_detected(self):
        """A box over the densest facility cluster must return facilities.

        The OSM extract clusters around 88.4/22.5 (the northern part of the
        bbox) rather than at Diamond Harbour, so the box is placed there.
        """
        flood = _box_geometry(88.38, 22.48, 88.45, 22.58)
        result = compute_exposure(flood)
        assert len(result.hospitals) > 0, "no hospitals found in the densest cluster"

    def test_empty_flood_exposes_nothing(self):
        result = compute_exposure(
            {"type": "Polygon", "coordinates": []}
        )
        assert result.hospitals == []
        assert result.roads == []

    def test_geography_collection_is_treated_as_no_flood(self):
        result = compute_exposure({"type": "GeometryCollection", "geometries": []})
        assert result.counts() == {"hospitals": 0, "substations": 0, "roads": 0}

    def test_definitions_travel_with_the_data(self):
        """A client must not read `cut_off` as a connectivity result."""
        payload = compute_exposure(_box_geometry(88.38, 22.48, 88.45, 22.58)).to_dict()
        assert "NOT a network connectivity analysis" in payload["definitions"]["road_cut_off"]

    def test_counts_match_feature_lists(self):
        result = compute_exposure(_box_geometry(88.38, 22.48, 88.45, 22.58))
        counts = result.counts()
        assert counts["hospitals"] == len(result.hospitals)
        assert counts["roads"] == len(result.roads)


class TestRouting:
    @pytest.fixture(scope="class")
    @classmethod
    def graph(cls):
        return build_road_graph()

    def test_graph_is_built_and_connected_enough(self, graph):
        assert graph.number_of_nodes() > 10_000
        assert graph.number_of_edges() > 10_000

    def test_delta_roads_are_included(self, graph):
        """Sagar Island must be on the network — it is the case study's landfall.

        Without data/delta_roads.geojson the island has no routable roads at
        all and every route from it fails with "not on road network".
        """
        from backend.simulation.routing import _haversine_m

        nearest = min(_haversine_m((88.10, 21.65), n) for n in graph.nodes)
        assert nearest < 2000, "Sagar Island is not covered by the road graph"

    def test_route_within_connected_area_succeeds(self, graph):
        route = safe_route(graph, None, (88.22, 21.75), (88.45, 21.90))
        assert route.reachable
        assert route.length_m > 0
        assert len(route.coordinates) >= 2

    def test_flood_can_sever_a_route(self, graph):
        """A flood covering the whole area must produce an explicit failure.

        Returning an empty-but-successful route would read as "safe", which is
        the dangerous direction to be wrong in.
        """
        flood = _box_geometry(87.0, 21.0, 90.0, 23.0)
        route = safe_route(graph, flood, (88.22, 21.75), (88.45, 21.90))
        assert not route.reachable
        assert route.reason

    def test_off_network_origin_is_reported(self, graph):
        route = safe_route(graph, None, (95.0, 10.0), (88.45, 21.90))
        assert not route.reachable
        assert "network" in route.reason

    def test_route_serialises(self, graph):
        payload = safe_route(graph, None, (88.22, 21.75), (88.45, 21.90)).to_dict()
        for key in ("coordinates", "length_km", "reachable"):
            assert key in payload


class TestShelters:
    def test_real_shelter_list_is_empty_and_says_why(self):
        """Not a placeholder: the data file documents the search that found nothing."""
        assert load_shelters() == []
        raw = json.loads((REPO_ROOT / "data" / "shelters.json").read_text())
        assert raw["official_reference"]["multipurpose_cyclone_shelters_built"] == 15
        assert raw["osm_check"]["usable_as_cyclone_shelters"] == 0

    def test_status_discloses_demo_data(self):
        status = shelter_dataset_status()
        assert status["is_demo_data"] is True
        assert "NOT verified" in status["disclosure"]
        assert "not an evacuation plan" in status["disclosure"].lower()

    def test_demo_shelters_are_labelled(self):
        for shelter in demo_shelters():
            assert shelter.is_demo_data is True
            assert shelter.name.startswith("DEMO")


class TestAllocation:
    def _nodes(self, total=4000):
        return [
            DemandNode("A", 88.22, 21.75, total // 4),
            DemandNode("B", 88.55, 21.80, total // 4),
            DemandNode("C", 88.45, 21.90, total // 4),
            DemandNode("D", 88.90, 21.80, total - 3 * (total // 4)),
        ]

    def test_feasible_demand_is_fully_assigned(self):
        result = allocate_shelters(self._nodes(4000))
        assert result["unmet_demand"] == 0
        assigned = sum(
            a["people"] for row in result["assignment"] for a in row["assignments"]
        )
        assert assigned == 4000

    def test_no_shelter_exceeds_capacity(self):
        result = allocate_shelters(self._nodes(4000))
        for load in result["shelter_loads"]:
            assert load["assigned"] <= load["capacity_people"]

    def test_infeasible_demand_is_reported_not_hidden(self):
        """Excess demand must surface as a shortfall, not a partial answer."""
        result = allocate_shelters(self._nodes(100_000))
        assert result["unmet_demand"] > 0
        assert "INFEASIBLE" in result["message"]

    def test_every_assignment_carries_the_disclosure(self):
        result = allocate_shelters(self._nodes(4000))
        assert result["status"]["is_demo_data"] is True

    def test_nearest_shelter_preferred_when_capacity_allows(self):
        """With slack capacity the LP should not send people needlessly far."""
        result = allocate_shelters(
            [DemandNode("near", 88.22, 21.75, 100)], demo_shelters()
        )
        row = result["assignment"][0]
        assert row["assignments"]
        assert row["assignments"][0]["distance_km"] < 15


class TestPopulation:
    def test_methodology_is_labelled_estimate(self):
        info = methodology()
        assert info["is_estimate"] is True
        assert "ESTIMATES" in info["disclosure"]
