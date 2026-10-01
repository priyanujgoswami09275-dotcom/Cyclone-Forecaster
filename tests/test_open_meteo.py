"""Open-Meteo as a supplementary context layer.

The tests that matter here are the ones about what the data is *not*: no API key
goes out, a missing field is not zero, a gust is not a sustained wind, and a
provider failure is disclosed rather than smoothed into an empty reading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.weather.open_meteo import (
    DEFAULT_HOURLY_VARS,
    MAX_FORECAST_DAYS,
    MIN_FORECAST_DAYS,
    OPEN_METEO_FORECAST_URL,
    OpenMeteoClient,
    OpenMeteoError,
    build_params,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# A complete `current` block, shaped like the real response.
# The grid cell deliberately differs from the point the tests request, because
# Open-Meteo snaps to a cell and a fixture that agreed with the request would
# make "which point is echoed?" untestable.
BODY = json.dumps(
    {
        "latitude": 21.68,
        "longitude": 88.09,
        "generationtime_ms": 0.2,
        "utc_offset_seconds": 0,
        "current": {
            "time": "2026-10-01T00:00",
            "interval": 900,
            "wind_speed_10m": 9.2,
            "wind_gusts_10m": 21.4,
            "wind_direction_10m": 214,
            "pressure_msl": 1004.3,
            "precipitation": 0.0,
        },
    }
)


def ok(_url: str) -> tuple[int, str]:
    return 200, BODY


# --- parameters -----------------------------------------------------------


def test_params_never_include_an_api_key() -> None:
    """Open-Meteo requires no key, and an invalid one returns 400.

    The absence is asserted rather than assumed: the failure mode is someone
    "fixing" a 401 by adding a key from the environment, which puts a secret in
    an outbound URL where it lands in proxy logs.
    """
    params = build_params(21.65, 88.06)
    assert "apikey" not in {k.lower() for k in params}
    assert not any("key" in k.lower() for k in params)
    assert OPEN_METEO_FORECAST_URL == "https://api.open-meteo.com/v1/forecast"


def test_params_prefer_the_sea_and_ask_for_every_variable() -> None:
    params = build_params(21.65, 88.06)
    assert params["cell_selection"] == "sea"
    assert params["timezone"] == "UTC"
    assert params["wind_speed_unit"] == "kmh"
    assert params["precipitation_unit"] == "mm"
    assert params["forecast_days"] == "2"
    for name in DEFAULT_HOURLY_VARS:
        assert name in params["current"]


def test_forecast_days_stays_inside_the_documented_range() -> None:
    for day in (0, 1, 7, 16):
        assert build_params(21.65, 88.06, forecast_days=day)["forecast_days"] == str(day)
    assert MIN_FORECAST_DAYS == 0 and MAX_FORECAST_DAYS == 16
    for bad in (-1, 17, 400):
        with pytest.raises(ValueError, match="forecast_days"):
            build_params(21.65, 88.06, forecast_days=bad)


def test_coordinates_are_rounded_rather_than_truncated() -> None:
    """4 dp, and rounded not floored, so a point does not drift north-east."""
    params = build_params(21.650049, 88.060049)
    assert params["latitude"] == "21.6500"
    assert params["longitude"] == "88.0600"
    assert build_params(21.6500599, 88.06)["latitude"] == "21.6501"


# --- failure is disclosed -------------------------------------------------


def test_a_400_becomes_a_disclosed_failure_not_empty_weather() -> None:
    """Bad variable, bad latitude and bad date all return 400 with a reason."""
    client = OpenMeteoClient(
        fetch=lambda url: (400, '{"error":true,"reason":"Invalid value"}')
    )
    with pytest.raises(OpenMeteoError) as excinfo:
        client.current(21.65, 88.06)
    assert excinfo.value.status == 400
    assert excinfo.value.reason == "Invalid value"


def test_a_failure_with_no_documented_shape_still_reports_something() -> None:
    """An unparseable body is quoted, never turned into silence."""
    client = OpenMeteoClient(fetch=lambda url: (503, "upstream unavailable"))
    with pytest.raises(OpenMeteoError) as excinfo:
        client.current(21.65, 88.06)
    assert excinfo.value.status == 503
    assert "upstream unavailable" in excinfo.value.reason


def test_an_empty_failure_body_is_still_not_silent() -> None:
    client = OpenMeteoClient(fetch=lambda url: (500, ""))
    with pytest.raises(OpenMeteoError) as excinfo:
        client.current(21.65, 88.06)
    assert excinfo.value.reason, "a failure with no stated reason is unactionable"


def test_any_transport_failure_propagates_rather_than_looking_like_calm() -> None:
    """An exception is not converted into an observation."""

    def explode(_url: str) -> tuple[int, str]:
        raise TimeoutError("timed out")

    with pytest.raises(TimeoutError):
        OpenMeteoClient(fetch=explode).current(21.65, 88.06)


# --- reading the payload --------------------------------------------------


def test_knots_are_never_reported_as_a_sustained_wind() -> None:
    """The two wind fields are different quantities and stay labelled."""
    observation = OpenMeteoClient(fetch=ok).current(21.65, 88.06)

    assert observation.wind_kmph == pytest.approx(9.2)
    assert observation.gust_kmph == pytest.approx(21.4)
    assert observation.interval_s == 900
    assert "10-minute mean" in observation.limitation
    assert "preceding hour" in observation.limitation
    assert "not a sustained wind" in observation.limitation


def test_missing_variable_in_the_payload_is_not_zero() -> None:
    """A response that omitted the field says nothing; `0` would be a claim."""
    body = json.dumps(
        {
            "latitude": 21.65,
            "current": {
                "time": "2026-10-01T00:00",
                "interval": 900,
                "wind_speed_10m": 9.2,
            },
        }
    )
    observation = OpenMeteoClient(fetch=lambda url: (200, body)).current(21.65, 88.06)

    assert observation.gust_kmph is None
    assert observation.pressure_hpa is None
    assert observation.direction_deg is None
    assert observation.precipitation_mm is None
    assert observation.wind_kmph == pytest.approx(9.2)
    assert "null rather than as zero" in observation.limitation


def test_a_real_zero_precipitation_is_kept_as_zero() -> None:
    """The distinction that matters: absent is None, reported-zero is 0."""
    observation = OpenMeteoClient(fetch=ok).current(21.65, 88.06)
    assert observation.precipitation_mm == 0.0
    assert observation.precipitation_mm is not None


def test_a_boolean_is_not_a_wind_speed() -> None:
    """`bool` subclasses `int`, so a naive isinstance check would accept True."""
    body = json.dumps({"current": {"wind_speed_10m": True, "pressure_msl": False}})
    observation = OpenMeteoClient(fetch=lambda url: (200, body)).current(21.65, 88.06)
    assert observation.wind_kmph is None
    assert observation.pressure_hpa is None


def test_the_model_is_not_invented_when_the_response_does_not_name_it() -> None:
    """Open-Meteo's `current` block names no model, so `model` stays null."""
    observation = OpenMeteoClient(fetch=ok).current(21.65, 88.06)
    assert observation.model is None

    named = json.dumps({**json.loads(BODY), "model": "ecmwf_ifs025"})
    assert (
        OpenMeteoClient(fetch=lambda url: (200, named)).current(21.65, 88.06).model
        == "ecmwf_ifs025"
    )


def test_an_empty_current_block_degrades_to_all_null_rather_than_crashing() -> None:
    observation = OpenMeteoClient(
        fetch=lambda url: (200, '{"current":{}}')
    ).current(21.65, 88.06)
    assert observation.wind_kmph is None
    assert observation.observed_at is None
    assert observation.interval_s is None


def test_the_requested_point_is_echoed_not_the_grid_cell() -> None:
    """The response is about where the caller asked, not where the grid landed."""
    observation = OpenMeteoClient(fetch=ok).current(21.65, 88.06)
    assert observation.latitude == pytest.approx(21.65)
    assert observation.longitude == pytest.approx(88.06)
    payload = json.loads(BODY)
    assert payload["latitude"] != observation.latitude, (
        "the fixture must differ from the request, or this test proves nothing"
    )


# --- the disclosure --------------------------------------------------------


def test_the_limitation_says_everything_a_reader_could_get_wrong() -> None:
    limitation = OpenMeteoClient(fetch=ok).current(21.65, 88.06).limitation
    assert "not an observation" in limitation
    assert "10-minute mean" in limitation
    assert "preceding hour" in limitation
    assert "cell_selection=sea" in limitation
    assert "No API key is used" in limitation
    assert "1.2 x (wind/115)^2" in limitation


def test_the_layer_never_feeds_the_surge_law() -> None:
    """AST, not grep: nothing under `backend/weather` may import the simulation."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "backend" / "weather"
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "simulation" not in node.module, f"{path.name}: {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "simulation" not in alias.name, f"{path.name}: {alias.name}"


def test_to_dict_is_json_serialisable_and_carries_the_disclosure() -> None:
    payload = OpenMeteoClient(fetch=ok).current(21.65, 88.06).to_dict()
    json.dumps(payload)  # must not raise
    assert payload["source"] == "Open-Meteo"
    assert payload["limitation"]
    assert set(payload) >= {
        "latitude",
        "longitude",
        "wind_kmph",
        "gust_kmph",
        "pressure_hpa",
        "observed_at",
    }

# --------------------------------------------------------------------------
# Every figure in this module's prose is pinned above
# --------------------------------------------------------------------------

from figure_guard import assert_figures_pinned, prose_figures  # noqa: E402

_MODULE_PY = REPO_ROOT / "backend" / "weather" / "open_meteo.py"


#: Figures in the module that are not measurements of anything. Each carries a
#: reason; an entry with an empty reason is itself a failure.
FIGURE_ALLOWLIST: dict[str, str] = {
    # A cross-reference to a MEMORY.md section, not a number this module
    # measured. It matches the guard only because a comma follows it and a comma
    # is indistinguishable from a thousands separator.
    "31": "a MEMORY.md section reference, matched only by the comma after it",
}


def test_every_measured_figure_in_this_module_is_pinned() -> None:
    """The guard from Task 2, applied to this module too.

    A new module that skips it is a module where the next hand-counted figure
    goes unnoticed, which is how six of them went unnoticed before.
    """
    assert_figures_pinned(_MODULE_PY, Path(__file__), FIGURE_ALLOWLIST)


def test_the_figure_invariant_bites(tmp_path) -> None:
    """Prove the guard fails on an unpinned figure rather than passing."""
    invented = "893" + "471"
    assert invented not in Path(__file__).read_text()

    synthetic = tmp_path / "synthetic.py"
    synthetic.write_text(
        '"' + '"' * 3 + "One unpinned figure." + '"' * 3 + "\n\n"
        "# It holds " + invented + " cells.\nVALUE = 1\n"
    )
    reported = [
        figure
        for figure, _line in prose_figures(synthetic)
        if figure not in Path(__file__).read_text()
    ]
    assert reported == [invented], f"guard did not report it; got {reported}"
