"""Render the flood extent to transparent PNGs for `<Overlay>` display.

    venv/bin/python -m backend.tools.render_overlays

    Outputs
    -------
    data/overlays/flood_cat0.png .. flood_cat6.png
    data/overlays/flood_remal_observed.png
    data/overlays/overlays.json      bounds + provenance for each image

Why this exists
---------------
`/surge-zone` returns the flood as GeoJSON. At category 6 that is 532
polygons, 40,698 rings and **180,038 vertices — 7.0 MB raw, 936 KB
gzipped**. It cannot back a slider on a phone: `react-native-maps` will
stutter drawing 180k vertices, and re-fetching 7 MB per slider step is a
connection problem before it is a rendering one.

A raster sidesteps both. The DEM is already a grid, the flood is already
computed on that grid, and a ~1000 px PNG is a few tens of KB. The map
places it with a geographic bounding box and never parses flood geometry at
all.

**This is display-only and it says so, loudly.** The PNG is a picture of the
modelled extent, not a queryable geometry layer:

  * `/exposure`, `/routes` and `/allocation` are untouched by this file and
    still consume the full-resolution mask. They are the computation; the
    overlay is the picture of it.
  * The raster is downsampled to ~1000 px, so it cannot represent the
    sub-pixel fragments `MIN_PART_KM2` drops, and it hard-quantises the
    depth classes below. Two cells with different depths can share a colour.
  * `alpha by depth class` is a **cartographic** encoding, not a measured
    field. It is four bands, not a continuous scale.

`overlays.json` therefore carries the modelled `final_land_area_km2` next to
the image, so a client can show the model's own number and label the raster
as the display layer rather than the source of the figure.

No new dependency: the PNG is encoded with stdlib `zlib` + `struct`. Pillow
would be a heavier way to write a file format this simple, and adding it for
an offline build step is not a trade worth making.

Pillow *is* now a dev dependency, for reading rather than writing. It was
added on 2026-09-30 after this encoder shipped an invalid IHDR colour type
(9, which the PNG spec does not define) in all eight images, and nothing
noticed for two days: the only thing that ever decoded these files was
`decode_png_rgba` in `tests/test_overlays.py`, which is the inverse of the
function that wrote them and had been taught to accept the bad value. A
decoder that mirrors its own encoder cannot disagree with it. Every overlay
is now opened by Pillow as well — a second, independent opinion about whether
these are images at all.

Per Rules.md nothing here fetches anything live — the DEM, the surge model
and the flood engine are all local and deterministic.
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import main  # noqa: E402
from backend.simulation.dem import load_dem  # noqa: E402
from backend.simulation.flood import run_flood_model  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data" / "overlays"
INDEX_PATH = OUT_DIR / "overlays.json"

#: Target width in pixels. `react-native-maps` <Overlay> is drawn by the map
#: engine sampling this texture, so more pixels than the widest phone screen
#: buys nothing and costs linear bytes. 1000 px across a ~150 km-wide bbox is
#: ~150 m/px, which is coarser than the 30 m DEM and coarser than the
#: ~170 m simplification tolerance the polygon path uses.
TARGET_WIDTH_PX = 1000

#: Depth classes, shallowest first: (upper bound in metres, alpha 0-255).
#:
#: Four bands, not a continuous ramp, because a continuous ramp on a 4-bit
#: quantised DEM is a lie about precision the data does not have — SRTM stores
#: whole metres, so every depth in a class is genuinely the same measurement.
#:
#: Alpha rises with depth so the deepest water reads as the most consequential
#: without needing a legend on the image. A permanent hazard tint over the
#: whole delta would make the shallow fringe look as bad as 4 m of water.
DEPTH_CLASSES: tuple[tuple[float | None, int], ...] = (
    (0.5, 70),  # 0 - 0.5 m
    (1.5, 110),  # 0.5 - 1.5 m
    (3.0, 160),  # 1.5 - 3.0 m
    (None, 210),  # > 3.0 m
)

#: Flood blue, same hue at every depth. A single hue keeps the ramp reading as
#: one quantity (how deep) rather than two competing ones (how deep *and*
#: how warm), which is what a multi-hue ramp would do on a hazard map.
RGB = (37, 99, 235)


def encode_png_rgba(width: int, height: int, rgba: np.ndarray) -> bytes:
    """Encode an (h, w, 4) uint8 array as a PNG, stdlib only.

    PNG's smallest useful form: 8-bit RGBA, no interlacing, one filter byte
    per scanline. Filter type 0 (None) everywhere — the alpha channel is mostly
    zero and the rows are spatially coherent, so the adaptive filters buy
    little here and every row costs a filter choice either way. zlib does the
    actual compression, and it is very good at long runs of transparent
    pixels, which is most of this image.
    """
    if rgba.shape != (height, width, 4) or rgba.dtype != np.uint8:
        raise ValueError(f"expected (h={height}, w={width}, 4) uint8, got {rgba.shape} {rgba.dtype}")

    raw = bytearray()
    for row in range(height):
        raw.append(0)  # filter type 0 = None
        raw += rgba[row].tobytes()

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # Colour type 6 = truecolour with alpha (RGBA), bit depth 8. PNG defines
    # only 0, 2, 3, 4 and 6; there is no 9. This file wrote 9 until
    # 2026-09-30, which produced eight files no conforming decoder accepts.
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def depth_alpha(depth_m: np.ndarray) -> np.ndarray:
    """Map water depth in metres to the alpha of its depth class.

    Returns uint8 alpha shaped like `depth_m`, zero where there is no water.
    """
    alpha = np.zeros(depth_m.shape, dtype=np.uint8)
    remaining = np.ones(depth_m.shape, dtype=bool)
    for upper, value in DEPTH_CLASSES:
        in_class = remaining & (depth_m > 0)
        if upper is not None:
            in_class &= depth_m < upper
        alpha[in_class] = value
        remaining &= ~in_class
    return alpha


def render_category(wind_kmph: float, label: str, slug: str, dem=None) -> dict:
    """Run the real flood engine for one intensity and write its overlay."""
    dem = dem or load_dem()
    result = run_flood_model(wind_kmph, dem=dem)
    final_frame = result.frames[-1]
    surge_m = result.surge.surge_m

    # The engine's final frame is the cumulative extent. Rebuild the raw
    # land mask here rather than reusing a polygonised version, because the
    # whole point of a raster overlay is to show the *unfiltered* extent —
    # including the sub-MIN_PART_KM2 fragments the polygon path drops, which
    # at low surge are most of what there is.
    from backend.simulation.flood import simulate_flood_propagation

    ocean = dem.ocean_mask()
    frames = simulate_flood_propagation(
        elevation=dem.elevation, ocean_mask=ocean, target_surge=surge_m
    )
    land_mask = frames[-1] & (~dem.nodata_mask) & ~ocean

    # Depth of standing water = peak surge minus ground elevation, floored at 0.
    depth = np.where(land_mask, np.maximum(0.0, surge_m - dem.elevation), 0.0)

    rows, cols = land_mask.shape
    height = max(1, round(rows * TARGET_WIDTH_PX / cols))

    # Nearest-neighbour subsample, and it is nearest rather than mean on
    # purpose. A mean over a boolean mask turns a 3-cell-wide inlet into a
    # 0.33-alpha smudge that effectively vanishes, and the inlet is exactly
    # what a flood map exists to show. Nearest keeps any sampled cell that
    # flooded visibly flooded, at the cost of overstating the water's edge by
    # up to one output pixel (~150 m here). For a display raster that is the
    # right way to err: an over-approximated edge is recoverable, a missing
    # inlet is not. Same reason the depth is sampled rather than averaged —
    # blending a 0 m cell into a 4 m one would understate the hazard at the
    # fringe, which is the most important place for that error to land.
    r_idx = (np.arange(height) * rows // height).clip(0, rows - 1)
    c_idx = (np.arange(TARGET_WIDTH_PX) * cols // TARGET_WIDTH_PX).clip(0, cols - 1)

    sub_mask = land_mask[r_idx][:, c_idx]
    sub_depth = depth[r_idx][:, c_idx]

    rgba = np.zeros((height, TARGET_WIDTH_PX, 4), dtype=np.uint8)
    rgba[..., 0] = RGB[0]
    rgba[..., 1] = RGB[1]
    rgba[..., 2] = RGB[2]
    rgba[..., 3] = depth_alpha(sub_depth)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png_bytes = encode_png_rgba(TARGET_WIDTH_PX, height, rgba)
    png_path = OUT_DIR / f"flood_{slug}.png"
    png_path.write_bytes(png_bytes)

    west, south, east, north = dem.bounds
    flooded_px = int(sub_mask.sum())
    return {
        "id": slug,
        "label": label,
        "wind_kmph": wind_kmph,
        "surge_m": round(surge_m, 4),
        "imd_category": result.surge.imd_category,
        "image": f"flood_{slug}.png",
        # Geographic bounds, the four numbers <Overlay> needs. The image
        # covers the whole DEM bbox, so these are the DEM's bounds and NOT
        # cropped to the flood — an overlay bigger than its content is what
        # lets the map place it without knowing the flood's extent.
        "bounds": {
            "west": round(west, 6),
            "south": round(south, 6),
            "east": round(east, 6),
            "north": round(north, 6),
        },
        "width_px": TARGET_WIDTH_PX,
        "height_px": height,
        "png_bytes": len(png_bytes),
        "flooded_pixels": flooded_px,
        "flooded_fraction_of_raster": round(flooded_px / (TARGET_WIDTH_PX * height), 6),
        # The model's own number, carried so the client never has to derive an
        # area from the picture it is drawing.
        "final_land_area_km2": round(final_frame.land_area_km2, 2),
        "drawn_area_km2": result.to_feature_collection()["drawn_area_km2"],
        "depth_classes_m": [
            {"upper": upper, "alpha": value} for upper, value in DEPTH_CLASSES
        ],
        "is_display_raster": True,
        "disclosure": (
            "Display raster, not a queryable geometry layer. Downsampled to "
            f"{TARGET_WIDTH_PX} px wide and quantised into {len(DEPTH_CLASSES)} "
            "depth classes, so it cannot represent the sub-pixel fragments the "
            "polygon path drops and two cells of different depth may share a "
            "colour. /exposure, /routes and /allocation use the full-resolution "
            "mask and are unaffected by this image. final_land_area_km2 is the "
            "model's figure; do not measure area from the picture."
        ),
    }


def main_entry() -> int:
    dem = load_dem()
    west, south, east, north = dem.bounds
    entries = []

    print(f"DEM {dem.shape[0]}x{dem.shape[1]}  bbox {west:.4f},{south:.4f} .. {east:.4f},{north:.4f}")

    for index in range(7):
        label, _lo, _hi, wind = main.category_band(index)
        entry = render_category(wind, label, f"cat{index}", dem=dem)
        entries.append(entry)
        print(
            f"  cat{index} {label:32} {wind:6.1f} kmph  "
            f"{entry['surge_m']:6.3f} m  land {entry['final_land_area_km2']:9.2f} km2  "
            f"{entry['png_bytes']:>8,} B  {entry['width_px']}x{entry['height_px']}"
        )

    # The case-study anchor. Not a category midpoint and equal to no band's
    # midpoint, which is exactly why it needs its own overlay.
    entry = render_category(
        main.ANCHOR_WIND_KMPH, "Remal as observed (May 2024)", "remal_observed", dem=dem
    )
    entries.append(entry)
    print(
        f"  preset {entry['label']:32} {main.ANCHOR_WIND_KMPH:6.1f} kmph  "
        f"{entry['surge_m']:6.3f} m  land {entry['final_land_area_km2']:9.2f} km2  "
        f"{entry['png_bytes']:>8,} B  {entry['width_px']}x{entry['height_px']}"
    )

    index_doc = {
        "generated_by": "backend/tools/render_overlays.py",
        "target_width_px": TARGET_WIDTH_PX,
        "dem_bbox": {
            "west": round(west, 6),
            "south": round(south, 6),
            "east": round(east, 6),
            "north": round(north, 6),
        },
        "surge_method": main.SURGE_METHOD if hasattr(main, "SURGE_METHOD") else "anchored_quadratic_scaling",
        "anchor": {"wind_kmph": main.ANCHOR_WIND_KMPH, "surge_m": main.ANCHOR_SURGE_M},
        "limitation": main.SURGE_LIMITATION,
        "is_display_raster": True,
        "disclosure": (
            "Every image here is a display raster. The flood engine's own "
            "full-resolution results are unchanged and are what /exposure, "
            "/routes and /allocation consume."
        ),
        "overlays": entries,
    }
    INDEX_PATH.write_text(json.dumps(index_doc, indent=2) + "\n")
    total = sum(e["png_bytes"] for e in entries)
    print(f"\nwrote {len(entries)} overlays to {OUT_DIR.relative_to(REPO_ROOT)}/")
    print(f"total PNG bytes: {total:,}  index: {INDEX_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_entry())
