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
from backend.ai.advisory import DistrictAdvisory, EvacuationPriority
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
        """A band is only useful if the wind it stands for is inside it.

        Category 6 is the exception the IMD table forces: Super Cyclonic Storm
        is documented as >=222 kmph with no upper bound, so there is no
        midpoint to take and `upper` is null. The representative wind is then
        the documented threshold, and `wind_is_band_midpoint` says so rather
        than letting a client assume a midpoint it was not given.
        """
        for category in client.get("/categories").json()["categories"]:
            band, wind = category["band_kmph"], category["wind_kmph"]
            assert band["lower"] <= wind, category
            if band["upper"] is None:
                assert wind == band["lower"], category
                assert category["wind_is_band_midpoint"] is False, category
            else:
                assert wind <= band["upper"], category
                assert category["wind_is_band_midpoint"] is True, category

    def test_band_knots_ships_beside_band_kmph(self, client):
        """IMD publishes both columns, so both travel.

        This is the guard on the unit bug: `band_kmph` briefly held the knots
        column (17/28/34/48/64/90/120) while being named and presented as
        km/h, which put every wind about 1.85x too low and shifted the whole
        category scale. Shipping the two columns side by side makes that
        checkable without reading the source: the ratio between them must be
        about 1.852 km/h per knot at every band.
        """
        for category in client.get("/categories").json()["categories"]:
            kmph, knots = category["band_kmph"], category["band_knots"]
            assert kmph["upper"] is None or kmph["upper"] > kmph["lower"]
            assert knots["upper"] is None or knots["upper"] > knots["lower"]
            if kmph["upper"] is None:
                assert knots["upper"] is None, category["category"]
            ratio = kmph["lower"] / knots["lower"]
            assert 1.7 < ratio < 1.95, (category["category"], ratio)

    def test_the_top_band_threshold_is_222_not_120(self, client):
        """Pins the specific number the bug got wrong.

        120 is IMD's *knot* threshold for Super Cyclonic Storm. The km/h
        threshold is 222. A regression to the knots column would put this back
        to 120 and shrink the slider's maximum surge by a factor of about 3.5.
        """
        top = client.get("/categories").json()["categories"][-1]
        assert top["band_kmph"]["lower"] == 222.0
        assert top["band_knots"]["lower"] == 120.0

    def test_out_of_range_category_is_422(self, client):
        for bad in (-1, 7, 99):
            for path in ("/surge-zone", "/exposure", "/allocation"):
                assert client.get(f"{path}?category={bad}").status_code == 422

    def test_non_numeric_category_is_422(self, client):
        assert client.get("/exposure?category=severe").status_code == 422

    def test_dead_zone_is_reported_not_hidden(self, client):
        """Bands the DEM cannot resolve must say so, not fake a surge.

        The retired regression produced a literal 0.0 for the weak bands
        because it clamped a negative extrapolation, so this test used to look
        for `surge_m == 0`. The scaling law cannot emit 0 for a non-zero wind
        — it is `1.2 * (w/115)^2`, positive everywhere above zero wind — so
        the dead zone is no longer a zero in the model. It is a surge smaller
        than the DEM's 1 m vertical quantum, which returns no inundation. The
        band must still say which case it is in.
        """
        categories = client.get("/categories").json()["categories"]
        assert all(c["surge_m"] > 0 for c in categories), "scaling law cannot emit 0"
        unresolved = [c for c in categories if c["note"]]
        assert unresolved, "the documented dead zone should still exist"
        for category in unresolved:
            assert "1 m" in category["note"], category["category"]
            # And the note only ever attaches where it is true.
            assert category["surge_m"] < 1.0, category["category"]
        # The resolved bands must not carry a stale caveat.
        resolved = [c for c in categories if c["surge_m"] >= 1.0]
        assert resolved, "at least the top bands should clear the DEM quantum"
        for category in resolved:
            assert category["note"] == "", category["category"]


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
        """Every surge number ships with the method, the anchor and the caveat.

        The retired regression carried `loo_mae_m` and `forward_speed_assumed`,
        which described a *fit* and an *assumed input*. There is no fit now and
        nothing is assumed, so those fields are replaced rather than kept as
        decoration — a client still reading them is reading a number that no
        longer means anything.
        """
        surge = high["surge"]
        assert surge["is_estimate"] is True
        assert surge["method"] == "anchored_quadratic_scaling"
        assert surge["anchor_wind_kmph"] == 115.0
        assert surge["anchor_surge_m"] == 1.2
        for retired in ("loo_mae_m", "forward_speed_assumed", "approach_angle_assumed",
                        "clamped", "raw_prediction_m"):
            assert retired not in surge, retired

    def test_top_level_caveats_travel_too(self, high):
        """The method/anchor/limitation block, not just the nested surge one."""
        assert high["method"] == "anchored_quadratic_scaling"
        assert high["anchor"]["wind_kmph"] == 115.0
        assert high["anchor"]["surge_m"] == 1.2
        assert high["anchor"]["event"]
        assert high["anchor"]["source"]
        for phrase in ("tide", "pressure", "bathymetry", "storm size"):
            assert phrase in high["limitation"], phrase


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

    def test_peak_band_floods_past_the_dem_quantum(self, high, client):
        """The peak band must clear more than the DEM's 1 m vertical quantum.

        This is the regression test for the knots-as-km/h bug. With the IMD
        thresholds read as knots, category 6's surge was 1.31 m: that reaches
        only cells at exactly 1 m elevation, a thin coastal fringe holding no
        mapped asset and shattering into sub-`MIN_PART_KM2` fragments. The
        flood drew nothing and `/exposure` returned zero. On the correct km/h
        column category 6 is 222 kmph / 4.47 m, which consolidates into real
        water bodies.

        The assertion is "> 0", not a pinned figure, because the exact area
        moves with the DEM. What must never come back is a drawable extent of
        zero at the top of the slider.
        """
        zone = client.get("/surge-zone?category=6").json()
        assert zone["final_land_area_km2"] > 0, "the peak band should still flood land"
        assert zone["drawn_area_km2"] > 0, (
            "the peak band must draw a polygon; a zero here means the surge "
            "has fallen back to the DEM's 1 m quantum again, which is what the "
            "knots-as-km/h bug did"
        )
        assert high["hospitals"]["count"] > 0
        assert high["substations"]["count"] > 0

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
        assert "not the district boundary" in info["disclosure"].lower()

    def test_localities_endpoint_carries_the_scoping(self, client):
        payload = client.get("/localities").json()
        assert payload["count"] == len(localities()) < len(all_localities())
        assert "scoping" in payload

    def test_every_case_study_town_is_addressable(self, client):
        ids = {loc["id"] for loc in client.get("/localities").json()["localities"]}
        for expected in ("kakdwip", "patharpratima", "namkhana", "gosaba", "sagar"):
            assert expected in ids, expected


class TestDistrictScoping:
    """Out-of-district places must not reach a real advisory.

    `data/places.geojson` carries no `admin_level` tags and no boundary
    geometry, so district membership is a decision this code makes and names,
    not a fact it can look up. Tamluk is in Purba Medinipur; it survived the
    old latitude-only scoping, reached `/allocation`, and was therefore in the
    Gemini prompt as a place needing evacuation on 24 Parganas advice.
    """

    def test_tamluk_is_not_served(self, client):
        ids = {loc["id"] for loc in client.get("/localities").json()["localities"]}
        assert "tamluk" not in ids

    def test_tamluk_is_not_in_the_allocation(self, client):
        """The allocation is what the advisory is written from, so this is the
        layer that actually mattered."""
        for category in (0, 6):
            nodes = {
                row["node"] for row in client.get(f"/allocation?category={category}").json()["allocation"]
            }
            assert "Tamluk" not in nodes, category

    def test_tamluk_is_not_routable(self, client):
        assert client.get("/routes?category=6&origin=tamluk").status_code == 404

    def test_the_denial_is_disclosed_with_the_real_district(self, client):
        """Dropped silently it would read as a data gap; named, it is a
        decision a human can check and reverse."""
        scoping_info = client.get("/localities").json()["scoping"]
        assert "Tamluk" in scoping_info["out_of_district_excluded"]
        assert "Purba Medinipur" in scoping_info["out_of_district_excluded"]["Tamluk"]
        assert "Tamluk" in scoping_info["excluded_names"]
        assert "Purba Medinipur" in scoping_info["excluded_reasons"]["Tamluk"]

    def test_every_deny_list_entry_exists_in_the_extract(self):
        """A typo in a deny-list key fails open: the place is still served and
        the list looks like it is working."""
        from backend.locations import scoping as scoping_info

        assert scoping_info()["deny_list_not_in_extract"] == []

    def test_unresolved_border_localities_are_kept_not_dropped(self):
        """The western boundary is genuinely undecidable from this data, and
        dropping a real delta village on a hunch is the worse error. They are
        kept, and the uncertainty is published rather than buried."""
        from backend.locations import BORDER_CLUSTER, localities, scoping

        names = {loc.name for loc in localities()}
        assert set(BORDER_CLUSTER) <= names, "a border-cluster place was dropped"
        info = scoping()
        assert list(BORDER_CLUSTER) == info["unresolved_border_localities"]
        assert "admin_level" in info["unresolved_border_note"]

    def test_scope_all_still_shows_it(self):
        """Deliberate: `?scope=all` is the raw extract, so an out-of-district
        place is visible with its real coordinates rather than absent. The
        advisory path never uses it."""
        from backend.locations import all_localities

        assert "Tamluk" in {loc.name for loc in all_localities()}

    def test_the_latitude_cut_is_no_longer_the_whole_story(self):
        """It is a coarse guard for the clipped Kolkata suburbs and says so; it
        is not presented as the district boundary."""
        from backend.locations import scoping as scoping_info

        info = scoping_info()
        assert info["study_area_districts"]
        assert "not the district boundary" in info["disclosure"].lower()
        assert "purba medinipur" in info["disclosure"].lower()


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

    def test_advisory_scopes_to_the_request_not_the_default_band(self, client, monkeypatch):
        """?category=3&scenario_id=observed must use Remal's observed wind, not cat3."""
        captured: dict = {}

        def generate(surge, exposure, allocation, context="", corrections=""):
            captured["surge"] = surge
            captured["exposure"] = exposure
            captured["allocation"] = allocation
            nodes = [row["node"] for row in allocation["allocation"]]
            return DistrictAdvisory(
                executive_summary=(
                    "Super Cyclonic Storm conditions over the delta. Shelter figures are "
                    "provisional placeholders, not surveyed."
                ),
                evacuation_plan=[
                    EvacuationPriority(
                        locality_name=name,
                        priority_level="CRITICAL",
                        reasoning="In the flood extent.",
                    )
                    for name in nodes
                ],
                sms_dispatch_draft=(
                    "Cyclone alert: evacuate low-lying areas now. Figures provisional."
                ),
                post_landfall_risks="Salinisation expected.",
                historical_context="Comparable in wind to Cyclone Amphan (2020).",
            )

        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setattr(main, "generate_advisory", generate)

        response = client.post("/advisory?category=3&scenario_id=observed&origin=kakdwip")
        assert response.status_code == 200, response.text

        assert "surge" in captured
        assert captured["surge"]["wind_kmph"] == pytest.approx(111.1, abs=0.05)
        assert captured["surge"]["wind_kmph"] != pytest.approx(103.0, abs=0.01)
        assert response.json()["generated_for"]["wind_kmph"] == pytest.approx(
            captured["surge"]["wind_kmph"], abs=0.001
        )
        assert response.json()["generated_for"]["scenario_id"] == "observed"
        assert response.json()["generated_for"]["cyclone_id"] == "2024145N14087"

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


class TestGzip:
    """GZipMiddleware — added because the flood polygon is 4.4 MB of text.

    The polygon is the largest thing this service sends and it is almost
    entirely coordinates, which gzip handles extremely well. The behaviour
    worth locking down is the *pair*: big responses compress, small ones are
    left alone, because below `minimum_size` the gzip framing costs more
    than it saves.
    """

    def test_a_large_response_is_gzipped(self, client):
        response = client.get(
            "/surge-zone?category=6", headers={"Accept-Encoding": "gzip"}
        )
        assert response.status_code == 200
        assert response.headers.get("content-encoding") == "gzip"
        # `Vary` is what tells a shared cache (Render's, a proxy's) that the
        # response differs by request header. Without it a cache can serve the
        # gzipped body to a client that did not ask for it.
        assert "accept-encoding" in response.headers.get("vary", "").lower()

    def test_gzip_actually_shrinks_the_flood_polygon(self, client):
        """Not just "gzip is on" — that it is worth having for this payload.

        Asserted on `Content-Length`, not `len(response.content)`: httpx
        transparently decodes a gzipped body, so `content` is the same
        4.4 MB either way and comparing it would prove nothing. The header is
        the wire size, which is the number that decides whether this is
        usable on a phone.
        """
        url = "/surge-zone?category=6"
        plain = client.get(url, headers={"Accept-Encoding": "identity"})
        zipped = client.get(url, headers={"Accept-Encoding": "gzip"})

        assert plain.headers.get("content-encoding") is None
        plain_len = int(plain.headers["content-length"])
        zipped_len = int(zipped.headers["content-length"])

        assert zipped_len < plain_len / 2, (
            f"gzip only got {plain_len} -> {zipped_len}; expected a large win"
        )

    def test_the_gzipped_body_decompresses_to_the_same_json(
        self, client, monkeypatch
    ):
        """Gzip must not change the bytes the client ultimately parses.

        Both bodies arrive already decoded by httpx, so this compares the
        decompressed payloads — which is exactly the equality that matters to
        a caller. The wire-level check is the Content-Length assertion above.

        The clock is frozen because `/surge-zone` now stamps `generated_at`
        when the body is built (provenance, added by b853406 — the pre-branch
        main returned no such field). Two requests a second apart then differ
        in that one field, and this test failed intermittently for a reason
        that has nothing to do with gzip: reproduced by sleeping 1.6 s between
        the calls, where only `generated_at` changed.

        `main.datetime` is frozen rather than the field dropped from the
        comparison, because excluding a key would quietly narrow the assertion
        this test names — every byte must survive the round trip, the
        timestamp included.
        """
        from datetime import datetime as _real

        class _Frozen:
            @classmethod
            def now(cls, tz=None):
                return _real(2026, 1, 1, 0, 0, 0, tzinfo=tz)

        monkeypatch.setattr(main, "datetime", _Frozen)

        url = "/surge-zone?category=6"
        plain = client.get(url, headers={"Accept-Encoding": "identity"})
        zipped = client.get(url, headers={"Accept-Encoding": "gzip"})

        assert zipped.content == plain.content
        assert zipped.json() == plain.json()
        # The freeze has to be shown to have taken effect, or the two equality
        # assertions above only passed because both calls landed in one second.
        assert plain.json()["generated_at"] == "2026-01-01T00:00:00+00:00"

    def test_a_small_response_is_left_uncompressed(self, client):
        """/health is a few hundred bytes; gzipping it is pure overhead."""
        response = client.get("/health", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.headers.get("content-encoding") is None
