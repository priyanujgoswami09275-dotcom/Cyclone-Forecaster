"""Wind speed -> storm surge height, by anchored quadratic scaling.

    surge_m = ANCHOR_SURGE_M * (wind_kmph / ANCHOR_WIND_KMPH) ** 2

This replaces a 3-feature LinearRegression that was fitted to four historical
Bay of Bengal cyclones. That model is archived, with the measurements that
displaced it, in `backend/experiments/surge_regression/`. The short version:
4 training rows and 4 fitted parameters (zero residual degrees of freedom)
gave a leave-one-out MAE of 2.36 m, and a predictor that simply returned the
mean of the training folds scored 0.90 m under the same splits — 2.6x better
than the model that was using its inputs.

Why scaling is the honest model here, not a downgrade
-----------------------------------------------------
With one observed event there is nothing to fit. A regression over four rows
spends its capacity memorising those four rows and then extrapolates
nonsense between and beyond them: it produced 0.062 m at 105 kmph and
3.863 m at 185 kmph, so the app's slider jumped straight past its own
case-study anchor and could never display it.

The scaling law has **no parameters to overfit**. It is monotone in wind by
construction, non-negative everywhere, needs no clamp (there is no lower or
upper bound to violate — it starts at zero and grows smoothly), and it
reproduces the anchor *exactly* rather than approximately.

The quadratic exponent
----------------------
Surge height scales steeply with wind because both the pressure deficit and
the total wind stress on the sea surface rise non-linearly with sustained
wind, and the water that is available to pile up grows with the storm's
duration and fetch. A square law is the standard first-order approximation
used in coastal screening, and it is deliberately the simplest form that
still captures that steepening: exponent 1 under-predicts badly at high wind,
and any exponent above ~3 would be claiming precision that one observation
cannot support.

The anchor
----------
**Cyclone Remal**, May 2024, landfall between Sagar Island (West Bengal) and
Khepupara (Bangladesh):

  * `ANCHOR_WIND_KMPH = 115.0` — the midpoint of the documented **110-120 kmph**
    landfall wind (gusting 135).
  * `ANCHOR_SURGE_M = 1.2` — the midpoint of the documented **~1.0-1.5 m**
    surge above astronomical tide.

Both ranges are as recorded in CLAUDE.md for the case study. Every response
carries them as an `anchor` block, and the limitation string, so no consumer
can present the output as anything but a screening estimate scaled from one
observed event.

What this does not model, and says so in every response: astronomical and
storm tide phase, atmospheric pressure, bathymetry and coastal geometry, storm
size and duration, and the approach angle. For a site-specific forecast none
of those omissions are acceptable; for triaging which assets sit in a hazard
zone across a whole district, the shape of the wind-to-surge relationship is
the dominant term and this is a defensible first cut.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache

# --------------------------------------------------------------------------
# The anchor — the single observed event this model is scaled from
# --------------------------------------------------------------------------

ANCHOR_WIND_KMPH = 115.0  # midpoint of the documented 110-120 kmph landfall wind
ANCHOR_SURGE_M = 1.2  # midpoint of the documented ~1.0-1.5 m surge

#: Identifies the method in every response. A client should be able to tell
#: what produced a number without reading this file.
SURGE_METHOD = "anchored_quadratic_scaling"

#: The one sentence that has to travel with every surge number. Restated per
#: response rather than left here, because a limitation that lives only in a
#: source file does not constrain anybody.
SURGE_LIMITATION = (
    "Screening estimate scaled from one observed event; omits tide, pressure, "
    "bathymetry and storm size."
)

# IMD wind classification, 3-min mean sustained wind in km/h.
# Source: India Meteorological Department, cyclone wind classification table.
# Ordered high-to-low at evaluation time.
IMD_CATEGORIES: tuple[tuple[float, str], ...] = (
    (120, "Super Cyclonic Storm"),
    (90, "Extremely Severe Cyclonic Storm"),
    (64, "Very Severe Cyclonic Storm"),
    (48, "Severe Cyclonic Storm"),
    (34, "Cyclonic Storm"),
    (28, "Deep Depression"),
    (17, "Depression"),
)


@dataclass(frozen=True)
class SurgeResult:
    """Surge prediction plus everything a caller needs to caveat it.

    Deliberately smaller than the retired regression's result: `clamped`,
    `raw_prediction_m`, `loo_mae_m`, `forward_speed_kmph`,
    `approach_angle_flag` and their `*_assumed` flags described a fitted
    model and have no meaning here. There is no fit, no error bar to report
    from a fit, no assumed features, and nothing to clamp.
    """

    wind_kmph: float
    imd_category: str
    surge_m: float
    method: str
    anchor_wind_kmph: float
    anchor_surge_m: float
    is_estimate: bool

    def to_dict(self) -> dict:
        return asdict(self)


def imd_category(wind_kmph: float) -> str:
    """IMD classification label for a 3-min mean wind in km/h."""
    for threshold, label in IMD_CATEGORIES:
        if wind_kmph >= threshold:
            return label
    return "Depression"


def surge_for_wind(wind_kmph: float) -> float:
    """The scaling law itself, as a bare function.

    `surge_m = 1.2 * (wind_kmph / 115) ** 2`

    Exposed separately so it can be checked directly by tests and reused by
    callers that only want the number. Non-negative for every `wind_kmph >= 0`
    and strictly increasing for `wind_kmph > 0`.
    """
    return ANCHOR_SURGE_M * (wind_kmph / ANCHOR_WIND_KMPH) ** 2


@lru_cache(maxsize=128)
def predict_surge(wind_kmph: float) -> SurgeResult:
    """Predict surge height for a sustained wind in kmph.

    Takes wind speed only. The retired regression took three features, two of
    which the app never had and had to assume.
    """
    wind = float(wind_kmph)
    if wind < 0:
        raise ValueError(f"wind_kmph must be >= 0, got {wind_kmph}")
    return SurgeResult(
        wind_kmph=wind,
        imd_category=imd_category(wind),
        surge_m=surge_for_wind(wind),
        method=SURGE_METHOD,
        anchor_wind_kmph=ANCHOR_WIND_KMPH,
        anchor_surge_m=ANCHOR_SURGE_M,
        # Always an estimate. Even at the anchor, 1.2 m is the midpoint of a
        # documented range scaled onto a single representative wind — it is not
        # an observation of the 1.2 m case.
        is_estimate=True,
    )
