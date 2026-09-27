"""Stamp provenance metadata into data/dem.tif (values unchanged).

The raster was fetched from Google Earth Engine:
    ee.Image("USGS/SRTMGL1_003")  clipped to the study bbox,
    getDownloadURL(crs="EPSG:4326", scale=50, format="GEO_TIFF")

`Rules.md` requires every input to trace to a real, named source, and the
GEE export carries no provenance of its own. This script rewrites the file
with identical pixel values plus descriptive tags. It is idempotent: if the
tags are already present it reports and exits without rewriting.

Run:  venv/bin/python backend/data_pipeline/tag_dem.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import rasterio

DEM_PATH = Path(__file__).resolve().parents[2] / "data" / "dem.tif"

SOURCE = "USGS/SRTMGL1_003 (SRTM 30m, void-filled), via Google Earth Engine"
FETCH_SCRIPT = "fetch_dem.py (repo root) — ee.Image.getDownloadURL, crs=EPSG:4326, scale=50"
NOTE = (
    "Real SRTM terrain, not synthetic. Elevations are metres above the EGM96 "
    "geoid; negative values are sea level and bays. Flood modelling uses these "
    "values directly — do not treat the raster as synthetic or resampled noise."
)


def main() -> None:
    if not DEM_PATH.exists():
        sys.exit(f"{DEM_PATH} not found — fetch the DEM before tagging it.")

    with rasterio.open(DEM_PATH) as src:
        if src.tags().get("source"):
            print(f"{DEM_PATH} already carries provenance tags; nothing to do.")
            return
        profile = src.profile
        data = src.read(1)
        tags = src.tags()

    tags.update(
        {
            "source": SOURCE,
            "srtm_derived": "true",
            "fetch_script": FETCH_SCRIPT,
            "bbox_west_south_east_north": "87.80,21.30,89.20,22.60",
            "crs": "EPSG:4326",
            "scale_m": "50",
            "units": "metres above EGM96 geoid",
            "nodata": "-32768",
            "note": NOTE,
            "tagged_by": "backend/data_pipeline/tag_dem.py",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )

    # Rewrite through a temp file so a failure can't truncate the DEM.
    tmp = DEM_PATH.with_suffix(".tif.tmp")
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(data, 1)
        dst.update_tags(**tags)

    with rasterio.open(tmp) as check:
        roundtripped = check.read(1)
        same = (
            check.shape == (profile["height"], profile["width"])
            and roundtripped.dtype == data.dtype
            and (roundtripped == data).all()
        )
    if not same:
        tmp.unlink(missing_ok=True)
        sys.exit("Verification failed: rewritten DEM differs from the original.")

    tmp.replace(DEM_PATH)
    print(f"Tagged {DEM_PATH} (pixel values unchanged).")


if __name__ == "__main__":
    main()
