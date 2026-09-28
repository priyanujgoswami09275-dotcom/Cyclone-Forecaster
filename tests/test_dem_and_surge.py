"""Tests for the DEM loader and the surge model (Module A inputs, Module B use).

The surge model is `surge_m = 1.2 * (wind_kmph / 115) ** 2` — anchored
quadratic scaling from one observed event (Cyclone Remal, May 2024), not a
fitted regression. The retired regression and the measurements that displaced
it are in `backend/experiments/surge_regression/`.

What these tests protect is the *character* of the model, not a set of
remembered outputs: that it hits its anchor exactly, that it is monotone, that
doubling wind quadruples surge, and that it never emits a number without the
method, the anchor, and the limitation string attached. A scaling law with
those properties cannot quietly start extrapolating, which is precisely how
the regression failed.
"""

import numpy as np
import pytest

from backend.simulation.dem import load_dem
from backend.simulation.surge import (
    ANCHOR_SURGE_M,
    ANCHOR_WIND_KMPH,
    SURGE_LIMITATION,
    SURGE_METHOD,
    imd_category,
    predict_surge,
    surge_for_wind,
)


class TestDemGeometry:
    """The DEM is EPSG:4326, so degrees are not metres. These pin the geo-math."""

    @pytest.fixture(scope="class")
    @classmethod
    def dem(cls):
        return load_dem()

    def test_lands_in_study_bbox(self, dem):
        west, south, east, north = dem.bounds
        # The GEE export was clipped to this bbox; allow a pixel of slack.
        assert west == pytest.approx(87.80, abs=0.01)
        assert south == pytest.approx(21.30, abs=0.01)
        assert east == pytest.approx(89.20, abs=0.01)
        assert north == pytest.approx(22.60, abs=0.01)

    def test_pixel_size_is_about_50m(self, dem):
        dx_m, dy_m = dem.metres_per_pixel()
        assert 30 < dx_m < 70
        assert 30 < dy_m < 70

    def test_metres_per_pixel_accounts_for_latitude(self, dem):
        """A degree of longitude is shorter than a degree of latitude here.

        At ~22 deg N, dx must be clearly less than dy; treating the pixel as
        square in degrees is the classic error that inflates east-west
        distances by ~20% in this region.
        """
        dx_m, dy_m = dem.metres_per_pixel()
        assert dx_m < dy_m * 0.95

    def test_cell_area_is_sane(self, dem):
        # ~50 m pixel => ~0.0025 km2. Allow generous slack for the 46x50 m
        # pixels the GEE export actually produced.
        assert 0.001 < dem.cell_area_km2() < 0.004

    def test_rowcol_roundtrip(self, dem):
        for lon, lat in [(88.10, 21.65), (88.45, 21.90), (88.06, 21.70)]:
            row, col = dem.lonlat_to_rowcol(lon, lat)
            back_lon, back_lat = dem.rowcol_to_lonlat(int(row), int(col))
            assert back_lon == pytest.approx(lon, abs=0.01)
            assert back_lat == pytest.approx(lat, abs=0.01)

    def test_known_places_have_coastal_elevations(self, dem):
        """Sanity check that row/col are not transposed or offset.

        Sagar Island and Diamond Harbour are low-lying delta islands; a
        transposed transform would put them at implausible elevations or
        outside the raster.
        """
        for lon, lat in [(88.10, 21.65), (88.45, 21.90)]:
            row, col = dem.lonlat_to_rowcol(lon, lat)
            assert 0 <= int(row) < dem.shape[0]
            assert 0 <= int(col) < dem.shape[1]
            assert -5 < dem.elevation[int(row), int(col)] < 30

    def test_ocean_mask_is_sea_level(self, dem):
        ocean = dem.ocean_mask()
        assert ocean.any(), "no ocean found — elevation or mask is broken"
        assert (dem.elevation[ocean] <= 0).all()
        # The Bay of Bengal dominates this bbox, so a large fraction is sea.
        assert 0.2 < ocean.mean() < 0.7

    def test_no_nodata_in_this_raster(self, dem):
        # SRTM GL1 is void-filled, so the GEE export should have none. If this
        # ever fails, the nodata-fill path in dem.py needs a real test.
        assert not dem.nodata_mask.any()


class TestImdCategories:
    @pytest.mark.parametrize(
        "wind,expected",
        [
            (17, "Depression"),
            (27, "Depression"),
            (28, "Deep Depression"),
            (34, "Cyclonic Storm"),
            (47, "Cyclonic Storm"),
            (48, "Severe Cyclonic Storm"),
            (63, "Severe Cyclonic Storm"),
            (64, "Very Severe Cyclonic Storm"),
            (89, "Very Severe Cyclonic Storm"),
            (90, "Extremely Severe Cyclonic Storm"),
            (119, "Extremely Severe Cyclonic Storm"),
            (120, "Super Cyclonic Storm"),
            (250, "Super Cyclonic Storm"),
        ],
    )
    def test_boundaries(self, wind, expected):
        assert imd_category(wind) == expected


class TestSurgePrediction:
    """The scaling law's defining properties. Not its output values."""

    def test_115_kmph_gives_exactly_1_2_m(self):
        """The anchor, exactly. Not approximately — exactly.

        This is the property the retired regression could not offer: it hit
        1.2 m at 115 kmph only because 115 kmph was one of its four training
        rows, and it missed by 1.479 m on leave-one-out. Here 115 kmph is the
        definition, not an interpolation.
        """
        assert predict_surge(115).surge_m == pytest.approx(1.2, abs=1e-9)
        assert surge_for_wind(115) == pytest.approx(1.2, abs=1e-9)

    def test_output_is_monotone_in_wind(self):
        """Non-decreasing everywhere, strictly increasing above zero wind."""
        values = [surge_for_wind(w) for w in np.arange(0, 300, 0.5)]
        assert all(b >= a for a, b in zip(values, values[1:]))
        positive = [v for w, v in zip(np.arange(0, 300, 0.5), values) if w > 0]
        assert all(b > a for a, b in zip(positive, positive[1:]))

    def test_doubling_wind_quadruples_surge(self):
        """The quadratic exponent, checked directly rather than assumed."""
        for wind in (20.0, 57.5, 80.0, 115.0, 200.0):
            assert surge_for_wind(wind * 2) == pytest.approx(
                surge_for_wind(wind) * 4, rel=1e-9
            )

    def test_never_negative_and_never_needs_a_clamp(self):
        """The retired model needed a [0, 4] clamp. This one cannot violate it.

        Checked across and beyond the slider's range, including winds above
        anything the app will ever send.
        """
        for wind in np.arange(0, 500, 1.0):
            assert surge_for_wind(wind) >= 0.0
        assert surge_for_wind(0) == 0.0

    def test_rejects_negative_wind(self):
        with pytest.raises(ValueError):
            predict_surge(-1)

    def test_always_flagged_as_estimate(self):
        """Even at the anchor, 1.2 m is a scaled midpoint, not an observation."""
        assert predict_surge(115).is_estimate is True
        assert predict_surge(200).is_estimate is True

    def test_result_carries_method_and_anchor(self):
        r = predict_surge(150)
        assert r.method == SURGE_METHOD == "anchored_quadratic_scaling"
        assert r.anchor_wind_kmph == ANCHOR_WIND_KMPH == 115.0
        assert r.anchor_surge_m == ANCHOR_SURGE_M == 1.2

    def test_limitation_names_what_the_model_omits(self):
        for phrase in ("tide", "pressure", "bathymetry", "storm size"):
            assert phrase in SURGE_LIMITATION
        assert "one observed event" in SURGE_LIMITATION

    def test_result_serialises_without_retired_fields(self):
        """The old shape is gone, and its absence is asserted, not assumed.

        `clamped`, `raw_prediction_m` and `loo_mae_m` described a fitted
        model. Leaving them in the payload would let a client keep reading
        numbers that no longer mean anything.
        """
        payload = predict_surge(120).to_dict()
        for key in ("wind_kmph", "surge_m", "is_estimate", "method",
                    "anchor_wind_kmph", "anchor_surge_m"):
            assert key in payload
        for retired in ("clamped", "raw_prediction_m", "loo_mae_m",
                        "forward_speed_kmph", "approach_angle_flag",
                        "forward_speed_assumed", "approach_angle_assumed"):
            assert retired not in payload

    def test_takes_wind_only(self):
        """The retired model took three features, two of them assumed.

        A one-argument call is the whole API now; anything else is a bug.
        """
        with pytest.raises(TypeError):
            predict_surge(115, 16, 1)  # type: ignore[call-arg]
