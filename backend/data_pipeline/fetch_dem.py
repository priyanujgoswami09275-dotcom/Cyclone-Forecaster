"""Fetch SRTM 30m DEM for the target bbox via Google Earth Engine -> data/dem.tif.

Source: ee.Image("USGS/SRTMGL1_003") clipped to the study bbox, exported
via getDownloadURL (one-shot pre-fetch; GEE is never called live at runtime).

Requires Earth Engine auth. If this fails with a credentials error, run:
    venv/bin/earthengine authenticate --auth_mode=notebook
(and register a cloud project at https://code.earthengine.google.com/register
if you don't have one) — then re-run this script.
"""

import io
import sys
import zipfile
from pathlib import Path

import ee
import requests

BBOX_RECT = [87.80, 21.30, 89.20, 22.60]  # west, south, east, north (EE order)
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "dem.tif"


def main() -> None:
    try:
        ee.Initialize()
    except Exception as exc:
        sys.exit(
            "Earth Engine init failed "
            f"({type(exc).__name__}: {exc}).\n"
            "Run `venv/bin/earthengine authenticate` and ensure a registered "
            "cloud project (https://code.earthengine.google.com/register), "
            "then re-run."
        )
    region = ee.Geometry.Rectangle(BBOX_RECT)
    image = ee.Image("USGS/SRTMGL1_003").clip(region)
    for scale in (30, 90):  # 30m may exceed getDownloadURL's size limit; 90m is the fallback
        try:
            url = image.getDownloadURL(
                {"scale": scale, "region": region, "format": "GEO_TIFF", "crs": "EPSG:4326"}
            )
        except Exception as exc:
            print(f"scale {scale} m rejected ({type(exc).__name__}: {exc}); trying next...")
            continue
        resp = requests.get(url, timeout=600)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            tif_name = next(n for n in zf.namelist() if n.endswith(".tif"))
            OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUT_PATH.write_bytes(zf.read(tif_name))
        print(f"Wrote {OUT_PATH} at {scale} m resolution")
        return
    sys.exit("GEE rejected the export at all attempted scales (30 m, 90 m).")


if __name__ == "__main__":
    main()
