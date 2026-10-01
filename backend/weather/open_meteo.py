"""Open-Meteo as a supplementary context layer — not as a cyclone source.

This module answers "what is the wind and pressure doing at this point right
now". It does not know what a cyclone is, does not track one, and does not feed
the surge law. Those come from IBTrACS and from
`1.2 x (wind_kmph/115)^2` respectively, and nothing here can reach either.

Every figure it produces carries a limitation string, because four separate
things about this data are easy to misread and all four are wrong to leave
unsaid:

**It is model output, not an observation.** Open-Meteo runs a numerical weather
model and serves its grid. There is no anemometer in the Bay of Bengal feeding
this. It is a good estimate of the state of the atmosphere, and calling it
"current conditions" implies an instrument that does not exist.

**Two of the numbers are not the same quantity.** `wind_speed_10m` is a
**10-minute mean**; `wind_gusts_10m` is the **maximum over the preceding hour**.
Reporting a gust as a sustained wind — or the mean as a gust — is the same
category error as the knots-read-as-km/h bug in MEMORY.md §31, one layer up. The
mean is never promoted to the gust field, and neither is rescaled.

**The grid cell was chosen to be over water.** `cell_selection=sea` biases the
cell toward the sea surface, which is the right bias when the question is about
a storm over the delta approaches. It is still a grid cell several kilometres
across, and it is not the point on the map.

**No API key is sent, because none is required.** `apikey` is absent from the
query by construction and a test asserts it. This is worth stating explicitly
because every other data source in this project needs a credential, and a reader
should not go looking in `.env` for one that does not exist.

## Failure is disclosed, never smoothed over

A non-200 raises `OpenMeteoError` carrying the status and the provider's own
`reason`. A missing variable in a 200 becomes `None` and **never `0`**: a gust
of zero is a claim about the atmosphere, and a response that omitted the field
says nothing at all. Collapsing those two is how "no data" becomes "calm".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: The documented `forecast_days` range. Open-Meteo returns 400 outside it, and
#: this raises before a request is made so the caller gets a clear error instead
#: of a provider's.
MIN_FORECAST_DAYS = 0
MAX_FORECAST_DAYS = 16

#: Seconds. A weather context layer that hangs is worse than one that fails.
DEFAULT_TIMEOUT_S = 10

DEFAULT_HOURLY_VARS: tuple[str, ...] = (
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "pressure_msl",
    "precipitation",
)


class OpenMeteoError(RuntimeError):
    """A disclosed failure, with the provider's own status and reason.

    Carries both rather than only a message, because "the weather layer is down"
    and "you asked for 17 forecast days" are different operational facts and the
    caller may want to answer them differently.
    """

    def __init__(self, status: int | None, reason: str) -> None:
        super().__init__(f"Open-Meteo returned {status}: {reason}")
        self.status = status
        self.reason = reason


def build_params(
    latitude: float, longitude: float, forecast_days: int = 2
) -> dict[str, str]:
    """Query parameters for one point. Strings, ready to urlencode.

    **Never includes `apikey`.** Open-Meteo requires no key for this endpoint;
    sending one that is absent or invalid is a 400, not a fallback. The absence
    is asserted by a test because the failure mode — someone "fixing" a 401 by
    adding a key from the environment — would put a secret in an outbound URL,
    where it lands in proxy logs and error reports.
    """
    if not MIN_FORECAST_DAYS <= forecast_days <= MAX_FORECAST_DAYS:
        raise ValueError(
            f"forecast_days must be {MIN_FORECAST_DAYS}..{MAX_FORECAST_DAYS}, "
            f"got {forecast_days}"
        )
    return {
        "latitude": f"{float(latitude):.4f}",
        "longitude": f"{float(longitude):.4f}",
        "current": ",".join(DEFAULT_HOURLY_VARS),
        "forecast_days": str(forecast_days),
        # The subject is a storm over water, so prefer the sea cell.
        "cell_selection": "sea",
        "timezone": "UTC",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }


def _number(value: object) -> float | None:
    """A float, or `None` for anything that is not a number.

    `isinstance` rather than a truthiness or try/except: `bool` is an `int`
    subclass and `True` is not a wind speed, so booleans are rejected outright.
    An absent field and a null field both become `None`, never `0`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class WeatherObservation:
    latitude: float
    longitude: float
    wind_kmph: float | None
    gust_kmph: float | None
    direction_deg: float | None
    pressure_hpa: float | None
    precipitation_mm: float | None
    observed_at: str | None
    interval_s: int | None
    model: str | None
    limitation: str

    def to_dict(self) -> dict:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "wind_kmph": self.wind_kmph,
            "gust_kmph": self.gust_kmph,
            "direction_deg": self.direction_deg,
            "pressure_hpa": self.pressure_hpa,
            "precipitation_mm": self.precipitation_mm,
            "observed_at": self.observed_at,
            "interval_s": self.interval_s,
            "model": self.model,
            "source": "Open-Meteo",
            "limitation": self.limitation,
        }


def _limitation(interval_s: int | None, complete: bool) -> str:
    """The disclosure, assembled so it cannot drift from the fields it describes."""
    missing = (
        ""
        if complete
        else " Some requested variables were absent from the response and are "
        "reported as null rather than as zero."
    )
    interval = (
        f" The averaging interval reported by the provider was {interval_s} seconds."
        if interval_s
        else ""
    )
    return (
        "Numerical weather model output, not an observation: there is no "
        "instrument at this point. wind_kmph is a 10-minute mean wind speed; "
        "gust_kmph is the maximum gust over the preceding hour and is not a "
        "sustained wind. The grid cell was chosen preferring water "
        "(cell_selection=sea), so it is a cell of several kilometres rather "
        "than the point requested. No API key is used, because this endpoint "
        "requires none. This is supplementary context and is not an input to "
        "the storm-surge figure, which comes from the deterministic law "
        "1.2 x (wind/115)^2."
        f"{interval}{missing}"
    )


def _default_fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S) as response:
            return int(response.status), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # An HTTPError is a response, not a crash: the status and body are the
        # most informative thing available, so they are returned for the caller
        # to turn into a disclosed failure.
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


class OpenMeteoClient:
    """Fetches one point's current context. Injectable `fetch` for tests."""

    def __init__(
        self, fetch: Callable[[str], tuple[int, str]] | None = None
    ) -> None:
        self._fetch = fetch or _default_fetch

    def current(self, latitude: float, longitude: float) -> WeatherObservation:
        params = build_params(latitude, longitude)
        url = f"{OPEN_METEO_FORECAST_URL}?{urllib.parse.urlencode(params)}"
        status, body = self._fetch(url)

        if status != 200:
            raise OpenMeteoError(status, _reason_from(body))

        payload = json.loads(body)
        block = payload.get("current") or {}
        wind = _number(block.get("wind_speed_10m"))
        gust = _number(block.get("wind_gusts_10m"))
        direction = _number(block.get("wind_direction_10m"))
        pressure = _number(block.get("pressure_msl"))
        precipitation = _number(block.get("precipitation"))

        interval = block.get("interval")
        interval_s = int(interval) if isinstance(interval, (int, float)) and not isinstance(interval, bool) else None
        complete = all(
            value is not None
            for value in (wind, gust, direction, pressure, precipitation)
        )

        return WeatherObservation(
            latitude=round(float(latitude), 4),
            longitude=round(float(longitude), 4),
            wind_kmph=wind,
            gust_kmph=gust,
            direction_deg=direction,
            pressure_hpa=pressure,
            precipitation_mm=precipitation,
            observed_at=block.get("time") if isinstance(block.get("time"), str) else None,
            interval_s=interval_s,
            # Open-Meteo's `current` block does not name the model it blended, so
            # this stays None rather than being filled with a plausible guess.
            model=payload.get("model") if isinstance(payload.get("model"), str) else None,
            limitation=_limitation(interval_s, complete),
        )


def _reason_from(body: str) -> str:
    """The provider's own reason, or its raw body when it is not the documented shape.

    Never empty: a failure with no stated reason is the hardest kind to act on,
    so a body that parses to nothing useful falls back to its own text.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return (body or "").strip()[:500] or "no reason supplied by the provider"
    if isinstance(payload, dict):
        for key in ("reason", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return (body or "").strip()[:500] or "no reason supplied by the provider"


__all__ = [
    "DEFAULT_HOURLY_VARS",
    "DEFAULT_TIMEOUT_S",
    "MAX_FORECAST_DAYS",
    "MIN_FORECAST_DAYS",
    "OPEN_METEO_FORECAST_URL",
    "OpenMeteoClient",
    "OpenMeteoError",
    "WeatherObservation",
    "build_params",
]