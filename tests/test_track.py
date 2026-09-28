"""Tests for GET /track — the historical cyclone track layer.

The track is real observed data for the case study, and the property worth
testing is the one the committed file makes hard: **a missing wind reading
is not a calm one.**

`backend/data_pipeline/fetch_ibtracs.py` coerces a blank `USA_WIND` to
`0.0` when it writes the GeoJSON, so five of the nineteen committed fixes
carry `usa_wind_kt: 0.0` that mean *not reported*. Every other test in this
repo is about not letting an artefact's convenience leak into a claim; this
file is the same concern one level down. A client that reads `0.0` as
0 knots draws a cyclone that stops dead mid-Bay and then restarts, which is
a visibly wrong thing to put on a map about a real cyclone.

So the assertions are grouped as:

  1. Missing wind is reported as missing, never as a number.
  2. The real committed file satisfies the shape the endpoint promises.
  3. Malformed input fails clearly and names the waypoint, rather than
     producing a track that is subtly wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACK_PATH = REPO_ROOT / "data" / "remal_track.geojson"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


@pytest.fixture(scope="module")
def track(client) -> dict:
    return client.get("/track").json()


def make_doc(waypoints: list[tuple[str, float, float, object]]) -> dict:
    """A minimal track file: (iso_time, lon, lat, usa_wind_kt) per fix.

    `usa_wind_kt` of `None` is written the way the fetch script writes a
    blank USA_WIND — as 0.0 — so a fixture exercises the real ambiguity
    rather than a shape the committed file never has.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for _t, lon, lat, _w in waypoints],
                },
                "properties": {
                    "name": "TEST",
                    "season": "2024",
                    "source": "IBTrACS v04r00",
                    "wind_units": "knots",
                },
            },
        ]
        + [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "iso_time": iso,
                    "usa_wind_kt": 0.0 if wind is None else wind,
                },
            }
            for iso, lon, lat, wind in waypoints
        ],
    }


@pytest.fixture
def write_track(tmp_path, monkeypatch):
    """Point main.TRACK_PATH at a temp file and return a setter for its body."""

    def _write(doc: dict) -> Path:
        path = tmp_path / "remal_track.geojson"
        path.write_text(json.dumps(doc))
        monkeypatch.setattr(main, "TRACK_PATH", path)
        return path

    return _write


class TestMissingWindIsNotCalm:
    """The reason this endpoint exists instead of a static file mount."""

    def test_a_blank_wind_reads_as_null_not_zero(self, write_track):
        write_track(make_doc([("2024-05-25 12:00:00", 89.4, 18.8, None)]))
        waypoint = main.load_track()["waypoints"][0]
        assert waypoint["wind_kt"] is None
        assert waypoint["wind_kmph"] is None
        assert waypoint["wind_reported"] is False

    def test_reported_wind_is_carried_in_both_units(self, write_track):
        write_track(make_doc([("2024-05-25 12:00:00", 89.4, 18.8, 54.0)]))
        waypoint = main.load_track()["waypoints"][0]
        assert waypoint["wind_kt"] == 54.0
        assert waypoint["wind_kmph"] == pytest.approx(100.0, abs=0.1)
        assert waypoint["wind_reported"] is True

    def test_a_reported_zero_wind_would_be_indistinguishable(self, write_track):
        """Documents why `reported` is a flag and not `wind_kt > 0`.

        If IBTrACS ever reports a genuine 0 kt — a genuinely calm fix — this
        implementation would call it unreported. That is the safe direction
        to be wrong in: showing 'not reported' where a 0 was measured
        understates nothing, while the reverse would draw a fabricated
        measurement. The fixture makes the trade explicit.
        """
        write_track(make_doc([("2024-05-25 12:00:00", 89.4, 18.8, 0.0)]))
        assert main.load_track()["waypoints"][0]["wind_reported"] is False

    def test_the_committed_file_really_does_contain_these_gaps(self, track):
        """Guards the premise: if the file is ever re-fetched clean, the
        null-handling above stops being load-bearing and this fails."""
        assert track["unreported_wind_count"] > 0
        assert any(w["wind_kt"] is None for w in track["waypoints"])

    def test_unreported_count_matches_the_waypoints(self, track):
        expected = sum(1 for w in track["waypoints"] if not w["wind_reported"])
        assert track["unreported_wind_count"] == expected


class TestCommittedFile:
    """The shape the endpoint promises, checked against real data."""

    def test_identity_and_provenance_survive_the_parse(self, track):
        assert track["name"] == "REMAL"
        assert track["season"] == "2024"
        assert track["source"] == "IBTrACS v04r00"
        assert track["wind_units"] == "knots"
        assert track["timezone"] == "UTC"

    def test_waypoints_are_chronological(self, track):
        stamps = [w["timestamp"] for w in track["waypoints"]]
        assert stamps == sorted(stamps)
        assert track["first_timestamp"] == stamps[0]
        assert track["last_timestamp"] == stamps[-1]

    def test_timestamps_are_rfc3339_utc(self, track):
        for waypoint in track["waypoints"]:
            assert waypoint["timestamp"].endswith("Z")
            assert "T" in waypoint["timestamp"]
            assert " " not in waypoint["timestamp"]

    def test_the_path_passes_through_every_waypoint(self, track):
        """A client draws the polyline and the markers separately. If the two
        views disagreed, the line would miss the pins it drew beside it."""
        assert len(track["path"]) == track["waypoint_count"]
        for segment, waypoint in zip(track["path"], track["waypoints"]):
            assert segment["latitude"] == waypoint["latitude"]
            assert segment["longitude"] == waypoint["longitude"]

    def test_coordinates_are_in_the_bay_of_bengal(self, track):
        """Not a tautology: lat/lon transposition is silent everywhere."""
        for waypoint in track["waypoints"]:
            assert 18.0 <= waypoint["latitude"] <= 25.0
            assert 88.0 <= waypoint["longitude"] <= 91.0

    def test_sequence_is_dense_and_ordered(self, track):
        assert [w["sequence"] for w in track["waypoints"]] == list(
            range(track["waypoint_count"])
        )

    def test_the_disclosure_names_the_null_convention(self, track):
        disclosure = track["disclosure"].lower()
        assert "not reported" in disclosure
        assert "ibtracs" in disclosure
        # The timezone is a sibling field, not part of the prose — a client
        # that renders these timestamps needs it, so it must be present.
        assert track["timezone"] == "UTC"

    def test_the_endpoint_succeeds(self, client):
        assert client.get("/track").status_code == 200

    def test_it_is_listed_in_the_root_endpoint_list(self, client):
        assert "GET /track" in client.get("/").json()["endpoints"]


class TestMalformedInput:
    """A bad file must fail clearly, not render a subtly wrong track."""

    def test_a_missing_file_is_a_named_500(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "TRACK_PATH", tmp_path / "nope.geojson")
        response = TestClient(main.app).get("/track")
        assert response.status_code == 500
        assert "is missing" in response.json()["detail"]
        assert "fetch_ibtracs" in response.json()["detail"]

    def test_invalid_json_names_the_file(self, tmp_path, monkeypatch):
        path = tmp_path / "remal_track.geojson"
        path.write_text("{not json")
        monkeypatch.setattr(main, "TRACK_PATH", path)
        response = TestClient(main.app).get("/track")
        assert response.status_code == 500
        assert "not valid JSON" in response.json()["detail"]

    @pytest.mark.parametrize(
        "doc,expected",
        [
            ({"type": "FeatureCollection"}, "no 'features'"),
            ({"type": "FeatureCollection", "features": []}, "no 'features'"),
            (
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [88, 21]},
                            "properties": {"iso_time": "2024-05-25 12:00:00"},
                        }
                    ],
                },
                "no LineString",
            ),
        ],
    )
    def test_structurally_wrong_files_are_rejected(self, write_track, doc, expected):
        write_track(doc)
        with pytest.raises(main.TrackDataError) as caught:
            main.load_track()
        assert expected in str(caught.value)

    def test_a_bad_timestamp_names_the_waypoint(self, write_track):
        doc = make_doc(
            [
                ("2024-05-25 12:00:00", 89.4, 18.8, 35.0),
                ("2024-05-26 12:00:00", 89.3, 18.75, 35.0),
                ("25/05/2024 3pm", 89.2, 18.7, 35.0),
            ]
        )
        write_track(doc)
        with pytest.raises(main.TrackDataError) as caught:
            main.load_track()
        message = str(caught.value)
        # The index counts every feature in the file, so the LineString at
        # index 0 makes the first waypoint index 1. What matters is that the
        # message points at the bad fix specifically, not merely that it
        # failed — a bare "invalid timestamp" sends you hunting.
        assert "waypoint 3" in message
        assert "25/05/2024 3pm" in message

    def test_line_and_points_disagreeing_is_rejected(self, write_track):
        """The two views of one track contradicting each other means a
        hand-edited or truncated file. A polyline that does not pass through
        the markers drawn beside it is worse than an error."""
        doc = make_doc([("2024-05-25 12:00:00", 89.4, 18.8, 35.0)])
        doc["features"][0]["geometry"]["coordinates"].append([89.3, 18.7])
        write_track(doc)
        with pytest.raises(main.TrackDataError) as caught:
            main.load_track()
        assert "disagree" in str(caught.value)

    def test_out_of_order_waypoints_are_sorted(self, write_track):
        """A track drawn backwards is a lie about which way the storm went."""
        write_track(
            make_doc(
                [
                    ("2024-05-27 12:00:00", 89.9, 23.6, 39.0),
                    ("2024-05-25 12:00:00", 89.4, 18.8, 35.0),
                ]
            )
        )
        waypoints = main.load_track()["waypoints"]
        assert waypoints[0]["timestamp"] == "2024-05-25T12:00:00Z"
        assert waypoints[-1]["timestamp"] == "2024-05-27T12:00:00Z"

    def test_it_is_not_keyed_by_category(self, client):
        """The track is a historical fact. If it ever grows a category
        parameter, a slider drag would be able to change a real event's
        track, which is the bug this test exists to prevent."""
        assert "category" not in client.get("/track").json()
