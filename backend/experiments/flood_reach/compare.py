"""Ocean-connected bathtub vs `run_flood_model` — NOT ON THE RUNTIME PATH.

Run from the repository root with:

    venv/bin/python backend/experiments/flood_reach/compare.py

Why this exists. The reference BFS in `simulate_flood_propagation` seeds from
the ocean, expands the flood by one DEM cell per step, and runs `n_steps=10`
steps. At this project's roughly ~50 m DEM that is a reach of at most about
600 m inland from any ocean-connected cell, regardless of the surge level.
This script measures what that reach limit costs at each band a judge can
pick, by computing the same quantity with no reach limit at all — a bathtub
fill: every cell with elevation <= surge that lies in a connected component
that touches ocean, with permanent ocean and nodata cells excluded from the
area.

Both sides exclude permanent ocean and nodata from the area, so the two
columns are the same quantity (inundated land). The only difference, by
construction, is how far the reference model is allowed to advance inland.

NOT a replacement for the model — see MEMORY.md "Flagged for review".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from backend.simulation.dem import load_dem
from backend.simulation.flood import run_flood_model
from backend.simulation.surge import IMD_BANDS

REMAL_SID = "2024145N14087"


def load_remal_wind() -> float:
    cat = json.loads((REPO_ROOT / "data" / "cyclones" / "catalogue.json").read_text())
    storm = next(r for r in cat["cyclones"] if r["cyclone_id"] == REMAL_SID)
    return float(storm["peak_wind_kmph"])


def bath_land_km2(dem, surge_m: float) -> float:
    """Flood land, ocean-connected, no reach limit. Same quantity as the model."""
    nodata = dem.nodata_mask
    ocean = dem.ocean_mask()
    low = (~nodata) & (dem.elevation <= surge_m)
    if not low.any():
        return 0.0
    labels, _ = ndimage.label(low)  # 4-connected, matching the BFS
    touching_ocean = set(int(v) for v in labels[ocean & low].tolist()) - {0}
    if not touching_ocean:
        return 0.0
    bath = np.isin(labels, list(touching_ocean)) & low
    land = bath & ~ocean & ~nodata
    return float(land.sum()) * dem.cell_area_km2()


def main() -> None:
    dem = load_dem()
    scenarios = [("Remal observed", load_remal_wind())]
    for idx in (4, 5, 6):
        scenarios.append((f"cat{idx}", IMD_BANDS[idx].representative_kmph()))

    header = f"{'scenario':<16} {'wind_kmph':>9} {'surge_m':>8} {'run_flood_model_km2':>19} {'bathtub_km2':>11} {'bath/model':>10}"
    print(header)
    print("-" * len(header))
    for label, wind in scenarios:
        result = run_flood_model(wind_kmph=wind, dem=dem)
        surge_m = result.surge.surge_m
        model_land = result.frames[-1].land_area_km2
        bath_land = bath_land_km2(dem, surge_m)
        ratio = bath_land / model_land if model_land else float("nan")
        print(
            f"{label:<16} {wind:>9.1f} {surge_m:>8.2f} "
            f"{model_land:>19.1f} {bath_land:>11.1f} {ratio:>10.2f}"
        )


if __name__ == "__main__":
    main()
