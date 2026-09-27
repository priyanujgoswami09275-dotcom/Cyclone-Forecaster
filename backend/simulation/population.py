"""At-risk population estimate per locality.

**This is an ESTIMATE and is labelled as one everywhere it is used.** Rules.md
requires that an estimate is never presented as an observed fact, so every
value this module produces carries `is_estimate: true` and the method used.

Method: OSM building footprint density, area-weighted by the flooded fraction
of each locality's extent.

    pop_locality ~= (buildings_in_extent / extent_km2) * flooded_km2 * POP_PER_BUILDING

Why this and not a population raster: WorldPop/GPWv4 would be more accurate,
but it needs a download this project has not made, and the honest thing is to
use a method that can be run and explained today while stating plainly that it
is a proxy. Swapping in a real raster later only changes `_buildings_per_km2`.

`POP_PER_BUILDING` is the weak link and is a documented assumption, not a
measurement: rural South 24 Parganas is largely low-rise, so ~5 persons per
residential building is a reasonable order of magnitude. It is a single
assumed constant and the response says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Point, shape
from shapely.prepared import prep

# ASSUMPTION, not measured. Mean persons per residential building in the
# low-rise delta districts. The dominant source of error in every population
# figure this module produces.
POP_PER_BUILDING = 5.0

# Below this many buildings a locality is too sparsely mapped for a density
# estimate to mean anything; it is reported with a null population rather than
# a number derived from two buildings.
MIN_BUILDINGS_FOR_ESTIMATE = 10


@dataclass(frozen=True)
class LocalityPopulation:
    name: str
    lon: float
    lat: float
    buildings: int
    extent_km2: float
    flooded_km2: float
    population: int | None
    is_estimate: bool = True
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "lon": self.lon,
            "lat": self.lat,
            "buildings": self.buildings,
            "extent_km2": round(self.extent_km2, 2),
            "flooded_km2": round(self.flooded_km2, 2),
            "population": self.population,
            "is_estimate": self.is_estimate,
            "note": self.note,
        }


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def estimate_populations(
    localities: list[tuple[str, float, float, float]],
    flood,
    cell_km2: float,
    buildings: list[tuple[float, float]],
) -> list[LocalityPopulation]:
    """Estimate exposed population for each locality.

    `localities` is [(name, lon, lat, radius_km)]. `buildings` is a list of
    (lon, lat) building centroids. `flood` is the flood geometry.
    """
    from shapely.ops import unary_union

    if flood is None:
        return [
            LocalityPopulation(n, lon, lat, 0, 0.0, 0.0, None, note="no flood extent")
            for n, lon, lat, _ in localities
        ]

    prepared = prep(flood)
    building_points = [Point(b) for b in buildings]
    results = []
    for name, lon, lat, radius_km in localities:
        centre = Point(lon, lat)
        # Locality extent as a simple degree box scaled by the radius.
        dlat = radius_km / 110.574
        dlon = radius_km / (111.320 * math.cos(math.radians(lat)))
        from shapely.geometry import box

        extent = box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)
        extent_km2 = abs(
            (2 * dlon * 111.320 * math.cos(math.radians(lat))) * (2 * dlat * 110.574)
        )
        if extent_km2 <= 0:
            continue

        n_buildings = sum(1 for b in building_points if extent.contains(b))
        flooded = extent.intersection(flood)
        flooded_km2 = flooded.area * (111_320**2) / 1e6 if not flooded.is_empty else 0.0

        if n_buildings < MIN_BUILDINGS_FOR_ESTIMATE:
            population = None
            note = (
                f"only {n_buildings} buildings mapped in a {extent_km2:.1f} km2 "
                f"extent; below the {MIN_BUILDINGS_FOR_ESTIMATE}-building floor "
                "for a density estimate"
            )
        else:
            density = n_buildings / extent_km2
            population = int(round(density * flooded_km2 * POP_PER_BUILDING))
            note = (
                f"ESTIMATE: {n_buildings} buildings / {extent_km2:.1f} km2 = "
                f"{density:.1f} bldg/km2 x {flooded_km2:.1f} km2 flooded x "
                f"{POP_PER_BUILDING:.0f} persons/building"
            )

        results.append(
            LocalityPopulation(
                name=name,
                lon=lon,
                lat=lat,
                buildings=n_buildings,
                extent_km2=extent_km2,
                flooded_km2=flooded_km2,
                population=population,
                note=note,
            )
        )
    return results


def methodology() -> dict:
    """Provenance block for any response carrying a population figure."""
    return {
        "method": "OSM building-footprint density, area-weighted by flooded fraction",
        "pop_per_building_assumption": POP_PER_BUILDING,
        "is_estimate": True,
        "disclosure": (
            f"Population figures are ESTIMATES derived from OSM building density "
            f"and an assumed {POP_PER_BUILDING:.0f} persons per building. They are "
            "not census figures and must not be used for real resource allocation."
        ),
    }
