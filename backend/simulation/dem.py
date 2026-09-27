"""Load and cache the SRTM DEM. Single source of truth for terrain + geo-math.

No other simulation module re-derives the transform, the CRS, or the
metres-per-pixel factor — they all take them from here, so a change of
resolution or extent cannot desynchronise the layers.

Provenance: data/dem.tif is real SRTM terrain (USGS/SRTMGL1_003) fetched via
Google Earth Engine; see backend/data_pipeline/tag_dem.py, which stamps that
into the raster's own tags. Per Rules.md nothing here fetches anything live.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import array_bounds

REPO_ROOT = Path(__file__).resolve().parents[2]
DEM_PATH = REPO_ROOT / "data" / "dem.tif"

# SRTM GL1 has no void-fill below sea level in this region, but the GEE export
# uses the standard -32768 nodata sentinel. Treat it as "no data" rather than
# as terrain 32 km below sea level, which would flood the entire bbox.
NODATA_SENTINEL = -32768


@dataclass(frozen=True)
class Dem:
    """A loaded DEM plus everything needed to map between pixels and metres."""

    elevation: np.ndarray  # (rows, cols) float32, metres, nodata already filled
    transform: rasterio.Affine
    crs: str
    nodata_mask: np.ndarray  # (rows, cols) bool — True where the source was nodata

    @property
    def shape(self) -> tuple[int, int]:
        return self.elevation.shape  # type: ignore[return-value]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) in degrees."""
        w, s, e, n = array_bounds(self.shape[0], self.shape[1], self.transform)
        return (w, s, e, n)

    @property
    def pixel_size_deg(self) -> tuple[float, float]:
        return abs(self.transform.a), abs(self.transform.e)

    def metres_per_pixel(self, latitude: float | None = None) -> tuple[float, float]:
        """Pixel size in metres as (dx, dy).

        The DEM is EPSG:4326, so a degree of longitude is not a fixed number of
        metres — it shrinks with the cosine of latitude. Callers that need a
        metric distance (the flood model's decay test, area computations) must
        go through this rather than assuming a square pixel in degrees.

        Latitude defaults to the middle of the raster, which is the right
        approximation for a bbox this small (~1.3 deg tall).
        """
        dx_deg, dy_deg = self.pixel_size_deg
        if latitude is None:
            _, south, _, north = self.bounds
            latitude = (south + north) / 2
        # WGS84 metres per degree of latitude/longitude at the given latitude.
        m_per_deg_lat = 111_132.92 - 559.82 * np.cos(2 * np.radians(latitude)) + \
            1.175 * np.cos(4 * np.radians(latitude))
        m_per_deg_lon = 111_412.84 * np.cos(np.radians(latitude)) - \
            93.5 * np.cos(3 * np.radians(latitude))
        return dx_deg * m_per_deg_lon, dy_deg * m_per_deg_lat

    def cell_area_km2(self) -> float:
        """Area of one pixel in km^2, at the raster's mid-latitude."""
        dx_m, dy_m = self.metres_per_pixel()
        return (dx_m * dy_m) / 1e6

    def rowcol_to_lonlat(self, row: int, col: int) -> tuple[float, float]:
        x, y = self.transform @ (col + 0.5, row + 0.5)
        return x, y

    def lonlat_to_rowcol(self, lon: float, lat: float) -> tuple[float, float]:
        """Continuous (fractional) row/col for a lon/lat. May be out of bounds."""
        col, row = ~self.transform @ (lon, lat)
        return row - 0.5, col - 0.5

    def ocean_mask(self) -> np.ndarray:
        """Cells at or below sea level (elevation <= 0). The BFS flood seed."""
        return (~self.nodata_mask) & (self.elevation <= 0.0)


@lru_cache(maxsize=1)
def load_dem(path: str | None = None) -> Dem:
    """Load the DEM once per process and cache it.

    The full 2898x3117 raster is ~9M cells; re-reading it per request would
    dominate API latency. Callers share the cached instance.
    """
    dem_path = Path(path) if path else DEM_PATH
    if not dem_path.exists():
        raise FileNotFoundError(
            f"DEM not found at {dem_path}. It is a pre-fetched, committed input "
            "by design (Rules.md: no live fetches) — restore it from git rather "
            "than downloading it at runtime."
        )
    with rasterio.open(dem_path) as src:
        raw = src.read(1).astype("float32")
        nodata = src.nodata
        transform = src.transform
        crs = str(src.crs)
    if nodata is None:
        nodata = NODATA_SENTINEL
    mask = raw == nodata
    # Fill nodata with the lowest real elevation rather than 0, so it never
    # registers as floodable terrain and never blocks propagation as a wall.
    if mask.any():
        real = raw[~mask]
        raw[mask] = float(real.min()) if real.size else 0.0
    return Dem(elevation=raw, transform=transform, crs=crs, nodata_mask=mask)
