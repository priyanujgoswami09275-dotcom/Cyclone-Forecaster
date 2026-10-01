"""What storm, and what strength — the two things every result depends on.

## Why this exists

Every number the app shows was cached on a category index alone. That was
correct when Remal was the only storm in it. It stops being correct the moment
there is a second one, in a way that produces **confidently wrong output rather
than an error**: two cyclones at the same strength resolve to the same wind, the
cache returns the first one's flood extent for the second, and the map draws a
different storm's water over the delta with nothing to indicate a mix-up.

So the unit of caching becomes `(cyclone_id, scenario_id)` and every result
carries the pair it was computed for.

## What a scenario is, and is not

A scenario is **a wind in km/h**. Not a label, not free text, not a category
index. It resolves to exactly one wind through the same `IMD_BANDS` table the
existing code already uses, so a category index and a scenario cannot drift
apart — the drift that produced the knots-as-km/h bug this project documents at
length (MEMORY.md §31).

`observed` is a scenario too: a cyclone's own peak wind. It is the one case
where the wind depends on the cyclone rather than on the scenario, which is
precisely why a category-keyed cache cannot hold it.

## The ML estimate is not here

`storm_peak_intensity` predicts a storm's peak wind, and it is a **second,
separately labelled figure** for the same scenario. It never feeds the surge
law and never touches these caches — the deterministic
`1.2 * (wind/115)**2` remains authoritative for every surge number the app
ships.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.cyclones.base import CycloneRecord, peak_wind_kmph
from backend.simulation.surge import IMD_BANDS

#: Remal's IBTrACS SID. The default everywhere, and the documented case study.
DEFAULT_CYCLONE_ID = "2024145N14087"

#: The default strength. Category 6 is the top IMD band and the only one that
#: exposes infrastructure at all, so it is what a reader sees first.
DEFAULT_SCENARIO_ID = "cat6"


@dataclass(frozen=True)
class Scenario:
    """One selectable strength, already resolved to a wind.

    `kind` matters because a category's "representative wind" is not always a
    midpoint: IMD documents no upper bound for the top band, so its value is the
    weakest qualifying wind and saying otherwise would overstate it. That
    distinction already rides on the API as `wind_is_band_midpoint` and is
    carried here rather than recomputed differently.
    """

    scenario_id: str
    label: str
    wind_kmph: float
    kind: str  # "observed" | "category" | "band_midpoint"
    imd_category: str
    wind_is_band_midpoint: bool
    limitation: str

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "label": self.label,
            "wind_kmph": self.wind_kmph,
            "kind": self.kind,
            "imd_category": self.imd_category,
            "wind_is_band_midpoint": self.wind_is_band_midpoint,
            "limitation": self.limitation,
        }


#: A category scenario is an assumption about an unobserved storm.
CATEGORY_LIMITATION = (
    "A chosen strength, not a forecast: it asks what this wind would do to the "
    "delta, and says nothing about whether any storm of this strength is "
    "expected."
)

OBSERVED_LIMITATION = (
    "The cyclone's own strongest reported wind, a record of what happened rather "
    "than a prediction. Where a fix reported no wind it is shown as unreported, "
    "not as calm."
)

_BAND_SCENARIOS: tuple[Scenario, ...] = tuple(
    Scenario(
        scenario_id=f"cat{index}",
        label=band.label,
        wind_kmph=band.representative_kmph(),
        kind="band_midpoint" if band.upper_kmph is not None else "category",
        imd_category=band.label,
        wind_is_band_midpoint=band.upper_kmph is not None,
        limitation=CATEGORY_LIMITATION,
    )
    for index, band in enumerate(IMD_BANDS)
)


def observed_scenario(cyclone: CycloneRecord | None) -> Scenario | None:
    """The cyclone's own peak, or `None` when nothing reported a wind.

    `None` rather than a fallback: a storm with no reported wind has no
    simulation to run, and substituting the nearest category would answer a
    question nobody asked.
    """
    if cyclone is None:
        return None
    wind = cyclone.peak_wind_kmph
    if wind is None:
        wind = peak_wind_kmph(cyclone.waypoints)
    if wind is None:
        return None
    return Scenario(
        scenario_id="observed",
        label=f"{cyclone.name} at its strongest",
        wind_kmph=wind,
        kind="observed",
        imd_category="observed",
        wind_is_band_midpoint=False,
        limitation=OBSERVED_LIMITATION,
    )


def scenarios_for(cyclone: CycloneRecord | None = None) -> tuple[Scenario, ...]:
    """The selectable strengths for one cyclone: its own, plus the bands."""
    observed = observed_scenario(cyclone)
    return (observed,) + _BAND_SCENARIOS if observed is not None else _BAND_SCENARIOS


def resolve_scenario(scenario_id: str, cyclone: CycloneRecord | None = None) -> Scenario:
    """Look up one scenario by id, naming the valid ids when it is not found.

    A typo raises rather than defaulting. Defaulting would show a reader a
    plausible scenario they did not ask for, which is the failure this whole
    address-on-(cyclone, scenario) design exists to prevent.
    """
    available = scenarios_for(cyclone)
    for scenario in available:
        if scenario.scenario_id == scenario_id:
            return scenario
    valid = ", ".join(s.scenario_id for s in available)
    raise KeyError(f"unknown scenario {scenario_id!r}. Available: {valid}")


@dataclass(frozen=True)
class ScenarioContext:
    """The pair every cache entry is keyed on.

    Frozen and hashable so it can be an `lru_cache` key directly. Its `hash` is
    over `cyclone_id` and `scenario_id` only — **not** over the resolved wind —
    which is deliberate: two scenarios that resolve to the same wind are still
    different scenarios, and `observed` reaching 115 km/h must not hand back the
    cache entry belonging to whichever band sits there.
    """

    cyclone_id: str = DEFAULT_CYCLONE_ID
    scenario_id: str = DEFAULT_SCENARIO_ID

    def __post_init__(self) -> None:
        for name in ("cyclone_id", "scenario_id"):
            if not getattr(self, name) or not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a non-empty string, got {getattr(self, name)!r}")

    @property
    def key(self) -> tuple[str, str]:
        return (self.cyclone_id, self.scenario_id)

    def resolve(self, cyclone: CycloneRecord | None = None) -> Scenario:
        return resolve_scenario(self.scenario_id, cyclone)

    def wind_kmph(self, cyclone: CycloneRecord | None = None) -> float:
        return self.resolve(cyclone).wind_kmph

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.cyclone_id}/{self.scenario_id}"


__all__ = [
    "CATEGORY_LIMITATION",
    "DEFAULT_CYCLONE_ID",
    "DEFAULT_SCENARIO_ID",
    "OBSERVED_LIMITATION",
    "Scenario",
    "ScenarioContext",
    "observed_scenario",
    "resolve_scenario",
    "scenarios_for",
]