"""The catalogue of cyclones the app can show, and the live probe beside it.

One object, so that "which storms exist" and "is anything happening now" have
the same answer everywhere. A registry per module would be two places to update
when a cyclone is added, and the two would eventually disagree.

## The catalogue is committed, the live status is not

`data/cyclones/catalogue.json` is generated from the IBTrACS archive by
`backend/data_pipeline/ingest_ibtracs_ni.py` and committed, so the historical
list is available offline and identical on every machine. The live status is
probed on every call and never cached: a stale "no cyclone running" is worse
than a slow one, because a reader who was told the basin was quiet an hour ago
has no way to know the feed has since gone down.
"""

from __future__ import annotations

import json
import logging
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

    def __init__(self, source: AtcfLiveSource | None = None) -> None:
        self._source = source

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
        """Probe now. Never cached — see the module note."""
        import asyncio

        try:
            return asyncio.run(self.source.probe())
        except Exception as exc:  # noqa: BLE001 - any failure is "unavailable"
            log.warning("live probe raised: %s", exc)
            from backend.cyclones.base import live_unavailable_reason
            from datetime import UTC, datetime

            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            return LiveStatus(
                status="live_unavailable",
                source=self.source.identifier,
                http_status=None,
                reason=f"{live_unavailable_reason(now)} The probe itself failed.",
                checked_at=now,
                endpoints=self.source.endpoints,
            )

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