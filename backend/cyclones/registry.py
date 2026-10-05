"""The catalogue of cyclones the app can show, and the live probe beside it.

One object, so that "which storms exist" and "is anything happening now" have
the same answer everywhere. A registry per module would be two places to update
when a cyclone is added, and the two would eventually disagree.

## The catalogue is committed, the live status is not

`data/cyclones/catalogue.json` is generated from the IBTrACS archive by
`backend/data_pipeline/ingest_ibtracs_ni.py` and committed, so the historical
list is available offline and identical on every machine. The live status is
cached for 60 s per registry: opening the app twice in a row must not probe the
feed twice, but a state is never served past that window — a stale "no cyclone
running" is worse than a slow one, and a reader must never read a century-old
probe as current. The whole `LiveStatus` is cached, including the
`live_unavailable` case, and the probe is never re-run until the window lapses.
"""

from __future__ import annotations

import time

import json
import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from backend.cyclones.base import CycloneRecord, CycloneWaypoint, LiveStatus
from backend.cyclones.historical import DEFAULT_IBTRACS_PATH, build_catalogue
from backend.cyclones.live import AtcfLiveSource
from backend.cyclones.scenarios import DEFAULT_CYCLONE_ID

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOGUE_PATH = REPO_ROOT / "data" / "cyclones" / "catalogue.json"

#: Why the catalogue exists and what it is not. Carried on every response that
#: lists cyclones, because "610 North Indian Ocean storms" invites the reading
#: that any of them could be selected for simulation, and only those that reach
#: this delta can.
CATALOGUE_LIMITATION = (
    "These are recorded storms from IBTrACS, not forecasts. Choosing one asks "
    "what its wind would do to this delta; it does not claim the storm affected "
    "these districts. Only storms whose track passes this coastline are "
    "meaningful here."
)


def _record_from_dict(item: dict) -> CycloneRecord:
    """Rebuild a record from its serialised form.

    The waypoints come back as plain dicts and have to become
    `CycloneWaypoint` again, or every consumer that reaches for
    `waypoint.wind_reported` gets an `AttributeError` on a dict — and
    `peak_wind_kmph`, which filters on that attribute, silently cannot run.
    """
    waypoints = tuple(CycloneWaypoint(**wp) for wp in item.get("waypoints", ()))
    return CycloneRecord(
        cyclone_id=item["cyclone_id"],
        name=item["name"],
        season=item["season"],
        basin=item["basin"],
        subbasin=item.get("subbasin"),
        source=item["source"],
        observed=item["observed"],
        waypoints=waypoints,
        fetched_at=item["fetched_at"],
        data_through=item.get("data_through"),
        peak_wind_kmph=item.get("peak_wind_kmph"),
        limitation=item["limitation"],
    )


@lru_cache(maxsize=1)
def _catalogue() -> tuple[CycloneRecord, ...]:
    """Every NI storm the catalogue holds, Remal first.

    Cached on nothing at all, which is correct and is the one exception to the
    rule this feature exists to enforce: a catalogue does not depend on which
    storm or scenario was asked about. Remal sorts first because it is the
    documented case study and the app's default, and a list whose first entry
    changes with the file's mtime would be a poor answer to "what am I looking
    at".
    """
    payload = None
    if CATALOGUE_PATH.exists():
        try:
            payload = json.loads(CATALOGUE_PATH.read_text())
        except json.JSONDecodeError as exc:
            log.warning("catalogue at %s is not valid JSON: %s", CATALOGUE_PATH, exc)
    if payload is None:
        # Regenerate rather than serve an empty list. The archive is a required
        # local input, so its absence raises loudly below rather than quietly.
        payload = build_catalogue(DEFAULT_IBTRACS_PATH)

    records = [_record_from_dict(item) for item in payload.get("cyclones", [])]
    records.sort(key=lambda r: (r.cyclone_id != DEFAULT_CYCLONE_ID, -r.season, r.name))
    return tuple(records)


class CycloneRegistry:
    """Cyclone lookup and the live probe, in one place."""

    def __init__(
        self,
        source: AtcfLiveSource | None = None,
        now: Callable[[], float] | None = None,
        live_ttl_seconds: float = 60.0,
    ) -> None:
        self._source = source
        # Injectable clock so a test can advance time without sleeping; monotonic
        # by default so a wall-clock jump cannot make a cached status look
        # eternally fresh or eternally stale.
        self._now = now or time.monotonic
        self._live_ttl_seconds = live_ttl_seconds
        self._live_cache: tuple[float, LiveStatus] | None = None

    @property
    def source(self) -> AtcfLiveSource:
        if self._source is None:
            self._source = AtcfLiveSource()
        return self._source

    def historical(self) -> tuple[CycloneRecord, ...]:
        return _catalogue()

    def get(self, cyclone_id: str | None) -> CycloneRecord | None:
        """A cyclone by id, or `None`.

        `None` rather than the default: a caller that asked for a storm which is
        not there should be told so. Silently substituting Remal here is the
        substitution this whole feature is built to forbid.
        """
        if not cyclone_id:
            return None
        for record in _catalogue():
            if record.cyclone_id == cyclone_id:
                return record
        return None

    def default_cyclone_id(self) -> str:
        """Remal, always. The app's default and its documented case study."""
        return DEFAULT_CYCLONE_ID

    def live_status(self) -> LiveStatus:
        """Probe, but cache the whole `LiveStatus` for `live_ttl_seconds`.

        **Cached for 60 s, including the unavailable case.** Opening the app
        twice within a minute is the common case, and a repeat probe would hit
        the network for the same answer. Past the window the probe runs again;
        a stale status must never be served as current. Passing a and `now` and
        `live_ttl_seconds` through to the constructor lets a test advance the
        clock without waiting.
        """
        now = self._now()
        if self._live_cache is not None:
            expires, status = self._live_cache
            if now < expires:
                return status

        import asyncio

        try:
            status = asyncio.run(self.source.probe())
        except Exception as exc:  # noqa: BLE001 - any failure is "unavailable"
            log.warning("live probe raised: %s", exc)
            from backend.cyclones.base import live_unavailable_reason
            from datetime import UTC, datetime

            checked = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            status = LiveStatus(
                status="live_unavailable",
                source=self.source.identifier,
                http_status=None,
                reason=f"{live_unavailable_reason(checked)} The probe itself failed.",
                checked_at=checked,
                endpoints=self.source.endpoints,
            )

        self._live_cache = (now + self._live_ttl_seconds, status)
        return status

    def describe(self) -> dict:
        """What `GET /cyclones` reports about where its list came from."""
        return {
            "dataset": "IBTrACS v04r01",
            "basin_filter": "NI",
            "subbasins": ["BB", "AS"],
            "generated_from": "ibtracs.NI.list.v04r01.csv",
            "live_source": self.source.identifier,
            "limitation": CATALOGUE_LIMITATION,
        }


@lru_cache(maxsize=1)
def _registry_singleton() -> CycloneRegistry:
    return CycloneRegistry()


def registry() -> CycloneRegistry:
    return _registry_singleton()


__all__ = [
    "CATALOGUE_LIMITATION",
    "CATALOGUE_PATH",
    "CycloneRegistry",
    "registry",
]