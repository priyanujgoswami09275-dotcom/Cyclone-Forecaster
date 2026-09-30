"""The Web basemap renderer and its committed output.

Two things are checked here, and the second one is the interesting one.

1. **The renderer is deterministic and display-only.** Same input raster, same
   output bytes — it has no model, no randomness and no network. And nothing in
   `backend/simulation/` may reference it, which is what keeps the promise that
   no figure in this app is measured off a picture.

2. **The committed PNG is opened by Pillow.** This is the MEMORY.md §42 lesson
   applied a second time: all eight flood overlays shipped with an invalid IHDR
   colour type (9, which the PNG spec does not define) for two days, and the
   suite passed the whole time, because the only decoder in the repo was the
   inverse of the function that wrote them and had been taught to accept the
   mistake. A decoder that mirrors its own encoder cannot disagree with it.
   Pillow is a completely independent implementation of the format, so it can
   say the file is not a PNG at all — which is the only property that makes the
   check worth having.

Pillow is a **dev** dependency and deliberately not in `requirements.txt`: the
service never opens a PNG, and adding it would put Pillow in the Vercel bundle
for no runtime reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO_ROOT / "data" / "basemap" / "basemap.json"
IMAGE_PATH = REPO_ROOT / "data" / "basemap" / "basemap.png"
THEME_PATH = REPO_ROOT / "mobile" / "theme.ts"
OVERLAYS_INDEX = REPO_ROOT / "data" / "overlays" / "overlays.json"

SIMULATION_DIR = REPO_ROOT / "backend" / "simulation"


@pytest.fixture(scope="module")
def index() -> dict:
    assert INDEX_PATH.exists(), (
        "data/basemap/basemap.json is missing — run "
        "`venv/bin/python -m backend.tools.render_basemap`"
    )
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


class TestTheCommittedBasemapExists:
    def test_the_index_and_image_are_both_committed(self, index):
        assert IMAGE_PATH.exists(), "run: venv/bin/python -m backend.tools.render_basemap"
        assert index["image"] == IMAGE_PATH.name

    def test_the_image_url_is_the_path_a_client_would_fetch(self, index):
        assert index["image_url"] == "/basemap/basemap.png"

    def test_the_bytes_on_disk_match_what_the_index_claims(self, index):
        # A stale index is worse than none: the Web build would place an image
        # using numbers that describe a different file.
        assert IMAGE_PATH.stat().st_size == index["png_bytes"]


class TestTheImageIsARealPng:
    """Pillow is the independent opinion. Nothing else here can disagree."""

    def test_pillow_opens_and_fully_decodes_it(self):
        Image = pytest.importorskip("PIL.Image")
        with Image.open(IMAGE_PATH) as img:
            img.load()  # forces an actual decode, not just a header read
            assert img.format == "PNG"
            assert img.mode == "RGBA", f"mode was {img.mode}"

    def test_the_size_is_what_the_index_claims(self, index):
        Image = pytest.importorskip("PIL.Image")
        with Image.open(IMAGE_PATH) as img:
            assert img.size == (index["width_px"], index["height_px"])

    def test_it_is_opaque_everywhere(self, index):
        """This is a base layer, not an overlay — a transparent pixel would be
        a hole in the map showing the page background through it."""
        Image = pytest.importorskip("PIL.Image")
        with Image.open(IMAGE_PATH) as img:
            alpha = np.asarray(img.convert("RGBA"))[..., 3]
        assert alpha.min() == 255, f"min alpha {alpha.min()}"
        assert index["is_display_raster"] is True

    def test_the_ihdr_colour_type_is_6_not_9(self):
        """The check that would have caught the eight invalid overlays.

        PNG defines colour types 0, 2, 3, 4 and 6. `render_overlays.py` wrote 9
        until 2026-09-30 and Pillow rejected every one of those files. This
        one is read straight out of the header bytes rather than through the
        project's own decoder, so it is structurally incapable of agreeing
        with a mistake in the encoder.
        """
        raw = IMAGE_PATH.read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        assert raw[24] == 8, "bit depth must be 8"
        assert raw[25] == 6, f"colour type was {raw[25]}, PNG defines 0/2/3/4/6"

    def test_it_contains_exactly_the_two_theme_tints(self, index):
        """Two colours, and both must be the ones the design specifies.

        A third would mean something other than the 0 m contour is being
        painted — a nodata cell treated as land, for instance.
        """
        Image = pytest.importorskip("PIL.Image")
        with Image.open(IMAGE_PATH) as img:
            colours = {c for _, c in img.convert("RGB").getcolors(maxcolors=64)}
        expected = {
            tuple(bytes.fromhex(index["land_colour"].lstrip("#"))),
            tuple(bytes.fromhex(index["water_colour"].lstrip("#"))),
        }
        assert colours == expected, f"unexpected colours: {colours ^ expected}"


class TestTheBasemapIsDerivedFromTheCommittedDem:
    def test_the_land_fraction_matches_a_fresh_read_of_the_dem(self, index):
        """Recomputed from `data/dem.tif` rather than trusted.

        This is the check that would catch the committed image drifting from
        the raster it claims to depict — a regenerated DEM, a hand-edited
        index, or a stale artefact.
        """
        import rasterio

        with rasterio.open(REPO_ROOT / "data" / "dem.tif") as src:
            elevation = src.read(1).astype("float32")
            elevation[elevation == src.nodata] = np.nan
            rows, cols = elevation.shape

            height = max(1, round(rows * index["width_px"] / cols))
            r_idx = (np.arange(height) * rows // height).clip(0, rows - 1)
            c_idx = (np.arange(index["width_px"]) * cols // index["width_px"]).clip(0, cols - 1)
            sub = elevation[r_idx][:, c_idx]

            land_fraction = float((sub > 0.0).mean())
            bounds = {
                "west": src.bounds.left,
                "north": src.bounds.top,
                "east": src.bounds.right,
                "south": src.bounds.bottom,
            }

        assert abs(land_fraction - index["land_fraction"]) < 1e-5
        for edge, value in bounds.items():
            assert abs(value - index["bounds"][edge]) < 1e-4, edge

    def test_the_land_fraction_is_the_real_sundarbans_delta(self, index):
        """A sanity floor, so a blank or fully-flooded image cannot pass.

        Measured on this raster: 59.01% land, 40.99% the Bay and the delta's
        tidal channels.
        """
        assert 0.4 < index["land_fraction"] < 0.75, index["land_fraction"]

    def test_it_provides_zero_o_means_the_dem_flood_seed(self, index):
        """0 m is not an arbitrary cut.

        `backend/simulation/dem.py` defines `ocean_mask()` as
        `elevation <= 0.0`, and that mask is the BFS flood seed. So this
        threshold is *the same* line the flood model floods from, which is why
        the basemap's coastline and the flood raster's extent agree by
        construction rather than by coincidence.
        """
        assert index["threshold_m"] == 0


class TestTheExtentRegistersWithTheFloodOverlays:
    def test_the_bounds_match_cat6_within_a_hundredth_of_a_millidegree(self, index):
        """They come from one DEM, so they must register.

        A divergence here would put the water somewhere the coastline is not —
        the exact failure the Web map cannot detect on screen.
        """
        overlays = json.loads(OVERLAYS_INDEX.read_text(encoding="utf-8"))
        cat6 = next(o for o in overlays["overlays"] if o["id"] == "cat6")
        for edge in ("west", "south", "east", "north"):
            assert abs(index["bounds"][edge] - cat6["bounds"][edge]) < 1e-4, edge

    def test_the_aspect_ratio_is_consistent_with_the_dem(self, index):
        # 2898 cols x 3117 rows, and at 22.5 N a degree of longitude is ~10%
        # shorter than a degree of latitude, so the pixel count ratio is not the
        # degree ratio. A generous bound that would still catch a transposed
        # width and height.
        aspect = index["width_px"] / index["height_px"]
        assert 0.9 < aspect < 1.2, aspect


class TestTheColoursAreTheDesignTokens:
    @pytest.mark.parametrize("token", ["land", "water"])
    def test_each_colour_is_read_out_of_theme_ts(self, index, token):
        """Parsed from the real file, not retyped here.

        A test that retypes the value it is checking is not a check — that is
        the same mistake as the self-written PNG decoder, and it is why this
        reads `theme.ts` instead of hardcoding `#e6ece0`.
        """
        source = THEME_PATH.read_text(encoding="utf-8")
        match = re.search(rf"\b{token}:\s*'(#[0-9a-fA-F]{{6}})'", source)
        assert match is not None, f"theme.ts has no {token} token"
        assert index[f"{token}_colour"].lower() == match.group(1).lower()

    def test_land_and_water_are_different_colours(self, index):
        # A single-colour image would pass every other test here.
        assert index["land_colour"] != index["water_colour"]


class TestTheRendererIsDeterministic:
    def test_rebuilding_reproduces_the_committed_bytes(self, index):
        """Same input raster, same output — no model, no clock, no randomness.

        The one thing a "derived artefact" has to be able to promise, and the
        reason the Web build can trust a committed PNG.
        """
        from backend.tools.render_basemap import encode_png_rgba, theme_map_tints

        import rasterio

        with rasterio.open(REPO_ROOT / "data" / "dem.tif") as src:
            elevation = src.read(1).astype("float32")
            elevation[elevation == src.nodata] = np.nan
            rows, cols = elevation.shape

        height = max(1, round(rows * index["width_px"] / cols))
        r_idx = (np.arange(height) * rows // height).clip(0, rows - 1)
        c_idx = (np.arange(index["width_px"]) * cols // index["width_px"]).clip(0, cols - 1)
        is_land = elevation[r_idx][:, c_idx] > 0.0

        land_rgb, water_rgb = theme_map_tints()
        rgba = np.zeros((height, index["width_px"], 4), dtype=np.uint8)
        for channel in range(3):
            rgba[..., channel] = np.where(is_land, land_rgb[channel], water_rgb[channel])
        rgba[..., 3] = 255

        rebuilt = encode_png_rgba(index["width_px"], height, rgba)
        assert rebuilt == IMAGE_PATH.read_bytes()


class TestItIsDisplayOnly:
    def test_nothing_in_the_simulation_layer_mentions_the_basemap(self):
        """The tripwire.

        The same guard `render_overlays` is held to: no file under
        `backend/simulation/` may contain the string "basemap", which is a
        crude but effective check against the picture quietly entering the
        computation path.
        """
        offenders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in SIMULATION_DIR.rglob("*.py")
            if "basemap" in path.read_text(encoding="utf-8").lower()
        ]
        assert offenders == [], f"simulation layer references the basemap: {offenders}"

    def test_the_index_says_it_is_a_raster_and_reads_no_numbers(self, index):
        assert index["is_display_raster"] is True
        assert index["reads_no_numbers"] is True

    def test_the_disclosure_names_the_derived_coastline_and_the_resolution(self, index):
        disclosure = index["disclosure"]
        assert "0 m contour" in disclosure
        assert "not from a surveyed coastline" in disclosure
        assert "150 m/px" in disclosure
        # The load-bearing sentence: no figure anywhere is measured off it.
        assert "no figure shown anywhere in this app is measured off it" in disclosure

    def test_it_records_its_own_provenance(self, index):
        assert index["source_dem"] == "data/dem.tif"
        assert "SRTMGL1_003" in index["source_dem_id"]
        assert index["generated_by"] == "backend/tools/render_basemap.py"
