"""Tests for backend/data_pipeline/fetch_osm_infra.py."""
from backend.data_pipeline.fetch_osm_infra import overpass_to_geojson


def test_overpass_nodes_and_ways_become_features():
    elements = [
        {"type": "node", "id": 1, "lat": 21.9, "lon": 88.1, "tags": {"amenity": "hospital"}},
        {
            "type": "way",
            "id": 2,
            "geometry": [{"lat": 21.9, "lon": 88.0}, {"lat": 22.0, "lon": 88.2}],
            "tags": {"highway": "primary"},
        },
        {"type": "way", "id": 3, "tags": {"highway": "trunk"}},  # no geometry -> skipped
    ]
    fc = overpass_to_geojson(elements)
    assert [f["geometry"]["type"] for f in fc["features"]] == ["Point", "LineString"]
    assert fc["features"][0]["properties"]["amenity"] == "hospital"


def test_way_geometry_becomes_lon_lat_linestring():
    elements = [
        {
            "type": "way",
            "id": 7,
            "geometry": [{"lat": 22.0, "lon": 88.0}, {"lat": 22.1, "lon": 88.1}],
            "tags": {"power": "substation"},
        }
    ]
    fc = overpass_to_geojson(elements)
    assert fc["features"][0]["geometry"]["coordinates"] == [[88.0, 22.0], [88.1, 22.1]]
    assert fc["features"][0]["properties"]["power"] == "substation"
    assert fc["features"][0]["properties"]["osm_id"] == 7


def test_empty_elements_give_empty_featurecollection():
    fc = overpass_to_geojson([])
    assert fc == {"type": "FeatureCollection", "features": []}
