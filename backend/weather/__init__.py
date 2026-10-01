"""The supplementary weather context layer. See `open_meteo.py`."""

from backend.weather.open_meteo import (
    DEFAULT_HOURLY_VARS,
    OPEN_METEO_FORECAST_URL,
    OpenMeteoClient,
    OpenMeteoError,
    WeatherObservation,
    build_params,
)

__all__ = [
    "DEFAULT_HOURLY_VARS",
    "OPEN_METEO_FORECAST_URL",
    "OpenMeteoClient",
    "OpenMeteoError",
    "WeatherObservation",
    "build_params",
]