"""Tests for the DEM loader and the surge model (Module A inputs, Module B use).

The surge model is weak by construction (n=4, LOOCV MAE 2.36 m), and these
tests exist to make sure its weaknesses stay *visible* rather than being
quietly smoothed over: the clamp, the estimate flag, and the MAE all have to
survive into the result, because Rules.md requires that no consumer can
mistake the number for an observation.
"""

import numpy as np
import pytest

from backend.simulation.dem import load_dem
from backend.simulation.surge import (
    SURGE_MAX_M,
    SURGE_MIN_M,
    imd_category,
    predict_surge,
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
    def test_remal_anchor_reproduces(self):
        """Remal's documented row: 115 kmph, 16 km/h forward, head-on -> 1.2 m."""
        result = predict_surge(115, 16, 1)
        assert result.surge_m == pytest.approx(1.2, abs=0.35)

    def test_output_is_always_clamped_into_range(self):
        for wind in range(20, 260, 5):
            r = predict_surge(wind)
            assert SURGE_MIN_M <= r.surge_m <= SURGE_MAX_M

    def test_clamp_flag_reports_actual_manipulation(self):
        # The weak fit goes negative at low wind; the clamp must be disclosed.
        low = predict_surge(31)
        assert low.raw_prediction_m < 0
        assert low.surge_m == SURGE_MIN_M
        assert low.clamped is True

    def test_clamp_flag_false_when_prediction_is_valid(self):
        mid = predict_surge(120, 15, 1)
        assert mid.raw_prediction_m > 0
        assert mid.surge_m == mid.raw_prediction_m
        assert mid.clamped is False

    def test_extreme_wind_is_capped(self):
        extreme = predict_surge(250)
        assert extreme.surge_m == SURGE_MAX_M
        assert extreme.clamped is True

    def test_always_flagged_as_estimate(self):
        """Even with all three features supplied, a 4-point fit is an estimate."""
        assert predict_surge(120, 15, 1).is_estimate is True

    def test_mae_is_carried_through(self):
        assert predict_surge(120).loo_mae_m == pytest.approx(2.36, abs=0.01)

    def test_defaults_are_flagged_as_assumed(self):
        r = predict_surge(120)
        assert r.forward_speed_assumed is True
        assert r.approach_angle_assumed is True
        supplied = predict_surge(120, 15, 1)
        assert supplied.forward_speed_assumed is False
        assert supplied.approach_angle_assumed is False

    def test_result_serialises(self):
        payload = predict_surge(120).to_dict()
        for key in ("wind_kmph", "surge_m", "is_estimate", "loo_mae_m", "clamped"):
            assert key in payload
