"""Tests for the display overlay layer (data/overlays + GET /overlays).

The overlays exist to make the map drawable on a phone. The property worth
testing is therefore not "the flood is correct" — the flood engine already
has its own tests against that — but the two claims the layer makes about
itself:

  1. **It is display-only.** `/exposure`, `/routes` and `/allocation` must
     not change because it exists. If a later round starts reading the
     raster (or its bounds) on the computation path, the API's numbers
     silently become a function of a rendering artefact.
  2. **It is honest.** The PNG is a quantised, downsampled picture. The
     index says so, carries the model's own area figure next to the image,
     and does not let a client measure area from the picture.

These run against the committed images in `data/overlays/`, because those are
what the app actually serves. Re-rendering takes ~40 s and is a manual build
step, so a test that re-rendered would test the renderer rather than the
artefact; the renderer's own logic (PNG encoding, depth classes) is checked
separately below without needing a full flood run.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import main
from backend.tools import render_overlays as ro

REPO_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_DIR = REPO_ROOT / "data" / "overlays"
INDEX_PATH = OVERLAY_DIR / "overlays.json"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


pytestmark = pytest.mark.skipif(
    not INDEX_PATH.exists(),
    reason=(
        "overlays have not been rendered; run "
        "`venv/bin/python -m backend.tools.render_overlays`"
    ),
)


@pytest.fixture(scope="module")
def index() -> dict:
    return json.loads(INDEX_PATH.read_text())


@pytest.fixture(scope="module")
def entries(index) -> dict[str, dict]:
    return {e["id"]: e for e in index["overlays"]}


def decode_png_rgba(path: Path) -> np.ndarray:
    """Minimal RGBA PNG decoder — the inverse of `encode_png_rgba`.

    Exists so the committed files can be checked as images rather than
    trusted as files. It handles exactly the subset the renderer writes
    (8-bit, colour type 6, filter 0), and asserts anything else, so a
    future change to the encoder cannot quietly produce something this
    silently misreads.

    **This is not sufficient on its own, and used to look like it was.**
    Between 2026-09-28 and 2026-09-30 the encoder wrote colour type 9 — not a
    value the PNG spec defines — and this decoder asserted `colour in (6, 9)`,
    on a comment claiming 9 was RGBA with tRNS disallowed and 6 was RGBA
    allowing it. Neither half of that was true. A decoder written as the
    inverse of an encoder, taught to tolerate that encoder's mistake, cannot
    find that mistake. `TestOverlaysAreRealImages` opens the same files with
    Pillow, which knows nothing about this project, and that is the check that
    would have caught it.
    """
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name}: bad PNG signature"
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos : pos + 4])
        tag = raw[pos + 4 : pos + 8]
        payload = raw[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", raw[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, f"{path.name}: CRC fail in {tag!r}"
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", payload[:10])
            # Colour type 6 = truecolour with alpha. PNG defines 0, 2, 3, 4
            # and 6 only -- there is no 9, and nothing about tRNS selects
            # between colour types. This asserts exactly 6.
            assert depth == 8, f"{path.name}: expected 8-bit, got {depth}"
            assert colour == 6, f"{path.name}: expected colour type 6, got {colour}"
        elif tag == b"IDAT":
            idat += payload
        pos += 12 + length
    data = zlib.decompress(idat)
    stride = width * 4
    rows = bytearray()
    for r in range(height):
        assert data[r * (stride + 1)] == 0, f"{path.name}: expected filter 0 on every row"
        rows += data[r * (stride + 1) + 1 : (r + 1) * (stride + 1)]
    return np.frombuffer(bytes(rows), dtype=np.uint8).reshape(height, width, 4)


class TestPngEncoding:
    """The encoder and decoder agree, and reject what they cannot handle."""

    def test_round_trips_exactly(self):
        rng = np.random.default_rng(7)
        rgba = rng.integers(0, 256, size=(17, 23, 4), dtype=np.uint8)
        png = ro.encode_png_rgba(23, 17, rgba)
        tmp = REPO_ROOT / "data" / "overlays" / "_roundtrip_tmp.png"
        try:
            tmp.write_bytes(png)
            assert np.array_equal(decode_png_rgba(tmp), rgba)
        finally:
            tmp.unlink(missing_ok=True)

    def test_rejects_a_mismatched_shape(self):
        with pytest.raises(ValueError):
            ro.encode_png_rgba(10, 10, np.zeros((9, 10, 4), dtype=np.uint8))

    def test_is_a_real_png_not_just_valid_zlib(self):
        assert ro.encode_png_rgba(4, 4, np.zeros((4, 4, 4), np.uint8)).startswith(
            b"\x89PNG\r\n\x1a\n"
        )


class TestDepthClasses:
    def test_boundaries_land_in_the_right_class(self):
        """`depth_alpha` is a stepped function, so its edges are the whole
        contract. Each class is [lower, upper): 0.5 m is already in the
        second band, 3.0 m already in the fourth, and 0 is never water."""
        d = np.array([0.0, 0.01, 0.49, 0.5, 1.49, 1.5, 2.99, 3.0, 4.4])
        by_depth = dict(zip(d.tolist(), ro.depth_alpha(d).tolist()))
        assert by_depth[0.0] == 0, "no water at zero depth"
        # Every depth inside one class shares its alpha...
        for same_class in [(0.01, 0.49), (0.5, 1.49), (1.5, 2.99), (3.0, 4.4)]:
            assert by_depth[same_class[0]] == by_depth[same_class[1]], same_class
        # ...and crossing a boundary steps up.
        for below, above in [(0.49, 0.5), (1.49, 1.5), (2.99, 3.0)]:
            assert by_depth[below] < by_depth[above], f"{below}..{above} boundary"
        # The four classes are used in order.
        ordered = [by_depth[d] for d in (0.01, 0.5, 1.5, 3.0)]
        assert ordered == sorted(ordered) and len(set(ordered)) == 4, ordered

    def test_alpha_increases_with_depth(self):
        """Deeper water must read as more consequential without a legend."""
        values = [a for _u, a in ro.DEPTH_CLASSES]
        assert values == sorted(values), values
        assert len(set(values)) == len(values), "depth classes must be distinguishable"

    def test_never_fully_opaque(self):
        """At alpha 255 the overlay would hide the basemap underneath it."""
        assert max(a for _u, a in ro.DEPTH_CLASSES) < 255


class TestOverlaysAreRealImages:
    """The independent opinion, from a decoder that has never seen this repo.

    Everything else in this file that decodes a PNG does so with
    `decode_png_rgba`, which is the inverse of the function that wrote the
    files. That is a closed loop: it agrees with the encoder by construction,
    and when the encoder wrote an invalid IHDR colour type the decoder was
    updated to accept the invalid value, so eight unopenable images passed
    every test here for two days.

    Pillow knows nothing about `encode_png_rgba`, about colour type 6, or
    about what this project intended. It either opens the file or it does not.
    That is the whole point, and it is why Pillow is a dev dependency rather
    than a runtime one: the service never decodes a PNG, only serves bytes.
    """

    def test_every_overlay_opens_with_pillow(self, entries):
        for entry in entries.values():
            path = OVERLAY_DIR / entry["image"]
            with Image.open(path) as img:
                img.load()  # forces the decode; open() alone only reads the header
            assert path.exists()

    def test_mode_is_rgba(self, entries):
        """Not palette, not greyscale-alpha, not RGB. `<Overlay>` composites
        the texture with per-pixel alpha, so anything without a real alpha
        channel either renders opaque or renders nothing."""
        for entry in entries.values():
            with Image.open(OVERLAY_DIR / entry["image"]) as img:
                assert img.mode == "RGBA", (
                    f"{entry['id']}: mode is {img.mode!r}, not 'RGBA'"
                )

    def test_size_matches_the_index(self, entries):
        """The index's `width_px`/`height_px` are what `<Overlay>` places the
        image by. If the file and the index disagree, the map places a texture
        of one size into bounds sized for another and the flood lands in the
        wrong place — silently, and only on a device."""
        for entry in entries.values():
            with Image.open(OVERLAY_DIR / entry["image"]) as img:
                assert img.size == (entry["width_px"], entry["height_px"]), (
                    f"{entry['id']}: file is {img.size}, "
                    f"index says {(entry['width_px'], entry['height_px'])}"
                )

    def test_an_alpha_channel_that_actually_varies(self, entries):
        """RGBA is a claim about the file, not a guarantee about the picture.
        The shallowest and deepest bands must both be present and different,
        or the alpha channel is being written as a constant and the depth
        ramp is a lie."""
        alphas = set()
        for entry in entries.values():
            if entry["flooded_pixels"] == 0:
                continue  # cat0-cat3 legitimately have no water at all
            with Image.open(OVERLAY_DIR / entry["image"]) as img:
                alphas.update(a for a in np.asarray(img)[..., 3].ravel() if a)
        assert len(alphas) > 1, f"alpha channel is constant across all overlays: {alphas}"

    def test_the_transparent_pixels_are_truly_transparent(self, entries):
        """The complement of the above, and the one that decides whether the
        overlay hides the basemap: dry land must have alpha exactly 0, not a
        small non-zero value that greys out the map underneath."""
        for entry in entries.values():
            with Image.open(OVERLAY_DIR / entry["image"]) as img:
                alpha = np.asarray(img)[..., 3]
            assert alpha.min() == 0, f"{entry['id']}: no fully transparent pixel"
            assert alpha.max() < 255, f"{entry['id']}: something is fully opaque"


class TestCommittedOverlays:
    def test_all_eight_are_present(self, entries):
        """Seven categories plus the Remal anchor. The anchor is not a
        category and has no index, so it is exactly the entry a client
        cannot discover from `/categories` alone."""
        assert set(entries) == {
            *(f"cat{i}" for i in range(7)),
            "remal_observed",
        }

    def test_every_image_exists_and_decodes(self, entries):
        for entry in entries.values():
            path = OVERLAY_DIR / entry["image"]
            assert path.exists(), f"{entry['id']}: missing {path.name}"
            rgba = decode_png_rgba(path)
            assert rgba.shape == (entry["height_px"], entry["width_px"], 4)

    def test_flooded_pixels_agree_with_the_documented_area(self, index, entries):
        """The image is a picture of the mask, so its pixel count has to be
        consistent with the area the model reported. A large disagreement
        would mean the overlay and the API are describing different floods —
        the one failure mode a client genuinely cannot detect."""
        # Rough km2 per output pixel at this latitude, from the DEM extent.
        west, east = index["dem_bbox"]["west"], index["dem_bbox"]["east"]
        south, north = index["dem_bbox"]["south"], index["dem_bbox"]["north"]
        lat_mid = (south + north) / 2
        km_per_deg_lat = 110.574
        km_per_deg_lon = 111.320 * np.cos(np.radians(lat_mid))
        km2_per_px = (
            (north - south) * km_per_deg_lat
            * (east - west) * km_per_deg_lon
            / (1000 * 930)
        )
        for entry in entries.values():
            expected = entry["final_land_area_km2"] / km2_per_px
            actual = entry["flooded_pixels"]
            # Nearest-neighbour sampling can only over-count, never
            # under-count, so allow the sampled figure to exceed the exact
            # one but not fall far below it.
            assert actual >= expected * 0.8, (
                f"{entry['id']}: {actual} px sampled vs {expected:.0f} px implied "
                f"by {entry['final_land_area_km2']} km2 — overlay and model disagree"
            )
            assert actual <= expected * 1.6, f"{entry['id']}: far more than expected"

    def test_intensity_increases_flooded_area(self, entries):
        """Monotone in surge. A raster layer that shrinks as the storm gets
        worse is a bug regardless of what the numbers say."""
        areas = [entries[f"cat{i}"]["final_land_area_km2"] for i in range(7)]
        assert areas == sorted(areas), areas
        for i in range(1, 7):
            assert areas[i] >= areas[i - 1]

    def test_low_categories_render_nothing_and_say_so(self, entries):
        """Cats 0-3 are below the DEM's 1 m quantum: the engine returns no
        inundation, and the overlay must be an all-transparent image rather
        than a faint suggestion of water that is not there."""
        for i in range(4):
            entry = entries[f"cat{i}"]
            assert entry["final_land_area_km2"] == 0.0
            rgba = decode_png_rgba(OVERLAY_DIR / entry["image"])
            assert rgba[..., 3].max() == 0, f"cat{i} has water in it"
            assert entry["png_bytes"] < 6000, f"cat{i} should be a near-empty PNG"

    def test_the_deepest_class_appears_where_the_depth_physically_allows_it(self, entries):
        """If the deepest class never appears, the depth encoding is
        decorative rather than informative — but it can only appear where
        the data makes it reachable.

        The renderer draws land, and SRTM is integer-valued in metres. So the
        deepest drawable land is the 1 m contour, and the achievable depth is
        `surge_m - 1 m`:

          * cat 6, surge 4.472 m -> 3.472 m deep, which clears the 3.0 m
            threshold, so all four classes must be present.
          * cat 5, surge 3.415 m -> 2.415 m deep, which is short of 3.0 m.
            Reaching that class would need ground below 0.415 m, and the only
            integer-metre cells that low are 0 m — ocean, which is excluded
            because the basemap already draws it.

        So cat 5 is expected to stop at the 1.5-3.0 m class. Asserting four
        classes there would be asserting an impossibility, and "fixing" the
        renderer until it passed would mean drawing the sea.
        """
        by_alpha = {a for _u, a in ro.DEPTH_CLASSES}
        for cat, expected in (("cat5", {70, 110, 160}), ("cat6", by_alpha)):
            alpha = decode_png_rgba(OVERLAY_DIR / entries[cat]["image"])[..., 3]
            present = set(np.unique(alpha).tolist()) - {0}
            assert present == expected, f"{cat}: got {sorted(present)}"

    def test_colour_is_constant_so_the_ramp_reads_as_one_quantity(self, entries):
        rgba = decode_png_rgba(OVERLAY_DIR / entries["cat6"]["image"])
        lit = rgba[rgba[..., 3] > 0]
        assert lit.size > 0
        assert np.all(lit[:, :3] == np.array(ro.RGB, dtype=np.uint8))


class TestHonestyDisclosure:
    def test_index_is_labelled_a_display_raster(self, index):
        assert index["is_display_raster"] is True
        disclosure = index["disclosure"].lower()
        assert "display raster" in disclosure
        assert "full-resolution" in disclosure
        # Wording differs between the index and the per-entry disclosure
        # ("unchanged" vs "unaffected"), so assert the claim rather than a
        # particular synonym for it.
        assert "unchanged" in disclosure or "unaffected" in disclosure
        for endpoint in ("/exposure", "/routes", "/allocation"):
            assert endpoint in disclosure, f"{endpoint} must be named as unaffected"

    def test_every_entry_carries_the_figures_a_client_must_not_derive(self, entries):
        """`final_land_area_km2` and `drawn_area_km2` are the model's numbers.
        A client that measured area from the PNG would get something else,
        so the index has to hand it the real figure and say which is which."""
        for entry in entries.values():
            assert "final_land_area_km2" in entry
            assert "drawn_area_km2" in entry
            assert "do not measure area from the picture" in entry["disclosure"].lower()

    def test_depth_classes_are_published(self, entries):
        for entry in entries.values():
            assert len(entry["depth_classes_m"]) == len(ro.DEPTH_CLASSES)
            assert "4" in entry["disclosure"] or str(len(ro.DEPTH_CLASSES)) in entry["disclosure"]

    def test_bounds_are_the_dem_bbox_and_are_not_a_crop(self, index, entries):
        """The overlay covers the whole DEM, so its bounds are the DEM's.
        Cropping to the flood would be a lie about what the image contains
        (and would make the image change size between categories)."""
        for entry in entries.values():
            for edge in ("west", "south", "east", "north"):
                assert entry["bounds"][edge] == pytest.approx(index["dem_bbox"][edge], abs=1e-6)

    def test_surge_provenance_travels_with_the_images(self, index):
        assert index["surge_method"] == "anchored_quadratic_scaling"
        assert index["anchor"] == {"wind_kmph": 115.0, "surge_m": 1.2}
        assert "screening estimate" in index["limitation"].lower()


class TestDisplayOnly:
    """The load-bearing property: the overlay layer changed no computation."""

    def test_overlay_directory_is_not_imported_by_the_simulation(self):
        source = (REPO_ROOT / "backend" / "simulation").rglob("*.py")
        for path in source:
            text = path.read_text()
            assert "overlay" not in text.lower(), (
                f"{path.name} references the overlay layer; the display raster "
                "must stay off the computation path"
            )

    def test_exposure_ignores_the_overlays(self):
        """`/exposure` counts assets inside the full-resolution mask. Its
        numbers must be identical whether or not the PNGs exist."""
        payload = main.exposure(6)
        assert payload["hospitals"]["count"] > 0
        assert "overlay" not in str(payload).lower(), (
            "/exposure must not report overlay-derived figures"
        )

    def test_allocation_and_routes_ignore_the_overlays(self):
        allocation = main.allocation(6)
        assert len(allocation["allocation"]) > 0
        assert "overlay" not in str(allocation).lower()
        routes = main.routes(6, "kakdwip")
        assert "overlay" not in str(routes).lower()


class TestEndpoint:
    def test_serves_every_overlay_with_absolute_urls(self, client):
        body = client.get("/overlays").json()
        assert body["count"] == 8
        for entry in body["overlays"]:
            assert entry["image_url"].startswith("/overlays/")
            assert (OVERLAY_DIR / entry["image"]).exists()

    def test_reports_the_disclosure(self, client):
        body = client.get("/overlays").json()
        assert body["is_display_raster"] is True
        assert "display raster" in body["disclosure"].lower()
        assert body["surge_method"] == "anchored_quadratic_scaling"

    def test_a_png_is_served_with_the_right_content_type(self, client):
        response = client.get("/overlays/flood_cat6.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert len(response.content) > 1000

    def test_a_missing_png_is_a_404_not_a_500(self, client):
        assert client.get("/overlays/flood_cat99.png").status_code == 404

    def test_overlay_ids_line_up_with_categories(self, client):
        """A client selects by category index, so `cat{i}` must exist for
        every index `/categories` reports, or a slider step has no image."""
        ids = {e["id"] for e in client.get("/overlays").json()["overlays"]}
        categories = client.get("/categories").json()["categories"]
        assert len(categories) == 7
        for cat in categories:
            assert f"cat{cat['category']}" in ids


class TestBoundsSanity:
    """The bounds are consumed positionally and never validated upstream.

    `react-native-maps` maps `bounds[0]` -> `northEast` and `bounds[1]` ->
    `southWest` without looking at the numbers, so a transposed or collapsed
    bounding box does not raise there. The image is placed against a mirrored
    box and, over this bbox, the result still looks like a plausible map with
    the flood somewhere the model never said. The failure is silent at every
    layer that could have caught it, which is why it is asserted at the
    boundary where the committed file is read.
    """

    GOOD = {"west": 87.799988, "south": 21.299954, "east": 89.200013, "north": 22.601613}

    def test_the_committed_index_passes(self, index):
        """The assertion is only worth having if today's data clears it."""
        for entry in index["overlays"]:
            main.assert_overlay_bounds(entry["id"], entry["bounds"])

    def test_correct_bounds_are_accepted(self):
        main.assert_overlay_bounds("cat5", dict(self.GOOD))

    @pytest.mark.parametrize(
        "bounds,expected",
        [
            # The transpositions. Either axis alone is enough to mirror the box.
            (
                {"west": 89.200013, "south": 21.299954, "east": 87.799988, "north": 22.601613},
                "west=89.200013, east=87.799988, expected west < east",
            ),
            (
                {"west": 87.799988, "south": 22.601613, "east": 89.200013, "north": 21.299954},
                "south=22.601613, north=21.299954, expected south < north",
            ),
        ],
    )
    def test_transposed_bounds_are_rejected_by_name(self, bounds, expected):
        with pytest.raises(main.OverlayBoundsError) as caught:
            main.assert_overlay_bounds("cat4", bounds)
        message = str(caught.value)
        assert "cat4" in message, f"the failing entry must be named, got {message!r}"
        assert expected in message

    @pytest.mark.parametrize("bounds", [
        # Degenerate, not transposed: a zero-area box is equally unplaceable,
        # and `>` rather than `>=` catches it for free.
        {"west": 88.5, "south": 21.299954, "east": 88.5, "north": 22.601613},
        {"west": 87.799988, "south": 21.9, "east": 89.200013, "north": 21.9},
    ])
    def test_collapsed_bounds_are_rejected(self, bounds):
        with pytest.raises(main.OverlayBoundsError):
            main.assert_overlay_bounds("remal_observed", bounds)

    @pytest.mark.parametrize("bounds", [
        {},
        {"west": 87.8, "south": 21.3, "east": 89.2},  # north missing
        {"west": 87.8, "south": 21.3, "east": None, "north": 22.6},
        {"west": "west", "south": 21.3, "east": 89.2, "north": 22.6},
    ])
    def test_malformed_bounds_are_rejected(self, bounds):
        with pytest.raises(main.OverlayBoundsError) as caught:
            main.assert_overlay_bounds("cat0", bounds)
        assert "cat0" in str(caught.value)

    def test_an_unnamed_entry_still_reports_something(self):
        with pytest.raises(main.OverlayBoundsError) as caught:
            main.assert_overlay_bounds("<unnamed>", {"west": 1, "south": 0, "east": 0, "north": 1})
        assert "<unnamed>" in str(caught.value)

    def test_the_endpoint_rejects_a_transposed_index(self, tmp_path, monkeypatch):
        """End to end, through `/overlays`, with a deliberately transposed
        fixture: the file on disk is fine, one entry in it is not, and the
        response must name which one rather than serve a mirrored box."""
        good = dict(self.GOOD)
        transposed = {**good, "west": good["east"], "east": good["west"]}
        index_doc = {
            "overlays": [
                {"id": "cat5", "image": "flood_cat5.png", "bounds": good},
                {"id": "cat6", "image": "flood_cat6.png", "bounds": transposed},
            ]
        }
        fake_dir = tmp_path / "overlays"
        fake_dir.mkdir()
        (fake_dir / "overlays.json").write_text(json.dumps(index_doc))

        monkeypatch.setattr(main, "OVERLAY_DIR", fake_dir)
        response = TestClient(main.app).get("/overlays")

        assert response.status_code == 500
        assert "cat6" in response.json()["detail"]
        # The well-formed entry is not the one named.
        assert "cat5" not in response.json()["detail"]
