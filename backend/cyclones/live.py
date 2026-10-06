"""The live cyclone provider: real ATCF, honest when it cannot be reached.

## What this is for

The app is a historical simulator for Cyclone Remal. A live layer lets it show
something happening now instead, which is the obvious thing a cyclone tool
wants and the hardest thing to get right, because "show something now" is also
the easiest thing to fake.

## No public North Indian Ocean ATCF source was reachable when this was written

Every candidate was probed from a server-side fetch on 2026-10-01. The results
are the reason `live_unavailable` is this provider's **expected default state**
and not an error path:

| Endpoint | Result |
|---|---|
| `nrlmry.navy.mil/atcf_web/docs/current_storms.txt` | 403 |
| `nrlmry.navy.mil/atcf_web/current_storms.txt` | 403 |
| `metoc.navy.mil/jtwc/jtwc.html` | 403 |
| `cira.colostate.edu/atlanticos/` | 403 |
| `nhc.noaa.gov/data/atcf/current_storms.txt` | 404 |
| `nhc.noaa.gov/CurrentStorms.json` | 200, but Atlantic/Eastern Pacific only |
| `nhc.noaa.gov/atcf/archive/2010..2025/` | 200, but **zero `io*.dat.gz` in every year** |
| `rsmcnewdelhi.imd.gov.in/json/`, `/pdf/` | 404 |
| `mausam.imd.gov.in/responsive/cyclone.php` | 404 |
| `rammb-data.cira.colostate.edu` | DNS does not resolve |

So the default endpoint list below is real and correct and **fails honestly**.
It is configurable, so the day RSMC New Delhi or an NRL mirror publishes a
machine-readable NI feed, this works with no code change.

## The rule this module exists to enforce

**`fetch()` returns `None` when no source is reachable. It never returns a
historical record.**

There is no import of `backend.cyclones.historical` here, and there must never
be one. A "live" mode that quietly showed Remal would be worse than having no
live mode at all: a judge would see a storm in the delta, assume it was current,
and be told something false by a tool whose entire value is that it is not. A
`None` and a refusal are visibly the same thing to the caller, which is exactly
what is wanted, so the compiler helps here — but the reasoning is recorded
because the next person to add a fallback will find it tempting.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Callable, Sequence

from backend.cyclones.atcf import parse_atcf, atcf_storm_id
from backend.cyclones.base import (
    LIVE_UNAVAILABLE_REASON,
    CycloneRecord,
    CycloneSource,
    CycloneWaypoint,
    LiveStatus,
    iso_time_to_rfc3339,
    live_unavailable_reason,
    no_active_storm_reason,
)

log = logging.getLogger(__name__)

#: Tried in order; the first that yields a North Indian Ocean storm wins.
#:
#: Kept as configuration rather than constants in the body so an operator can
#: add an endpoint without touching code — the whole reason this provider is
#: written when no endpoint works.
DEFAULT_ATCF_ENDPOINTS: tuple[str, ...] = (
    "https://www.nrlmry.navy.mil/atcf_web/docs/current_storms.txt",
    "https://www.nrlmry.navy.mil/atcf_web/current_storms.txt",
    "https://www.nhc.noaa.gov/data/atcf/current_storms.txt",
    "https://www.nhc.noaa.gov/CurrentStorms.json",
)

#: The basin this app is about. Everything else is filtered out rather than
#: drawn: showing an Atlantic storm over the Sundarbans would be the most
#: spectacular wrong answer available from this feature.
TARGET_BASIN = "IO"

#: What a live fix is and is not. ATCF's wind is a 1-minute mean sustained
#: wind in knots; the IMD bands the rest of this app uses are 3-minute means.
#: Presenting one as the other is a real error, not a rounding difference.
ATCF_LIMITATION = (
    "Positions are ATCF operational fixes, in the order the feed published them. "
    "This is a live observation, not a forecast, and not a track of what the "
    "storm will do. ATCF sustained wind is a 1-minute mean in knots, while the "
    "strength bands elsewhere in this app are IMD 3-minute means, so the two "
    "are not directly comparable. An operationally assigned fix is not a "
    "deterministic forecast."
)


class TransportError(RuntimeError):
    """A source could not be read. Never a reason to substitute anything."""


def _default_transport(url: str, timeout_s: float) -> tuple[int, str]:
    """Fetch `url`, returning `(status, body)`. Status 0 means unreachable."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "cyclone-forecaster/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except Exception as exc:  # noqa: BLE001 - any failure is "unavailable"
        log.warning("live source %s failed: %s", url, exc)
        return 0, ""


class AtcfLiveSource(CycloneSource):
    """Reads a live North Indian Ocean storm from a configurable ATCF endpoint.

    The `transport` parameter exists so the whole provider can be exercised
    without a network. It is a parameter rather than a monkeypatch because the
    failure paths are the interesting ones — 403, 404, a timeout, a 200 that is
    not ATCF — and they need to be tested every run, not only when NOAA is
    reachable.
    """

    identifier = "atcf_live"

    def __init__(
        self,
        endpoints: Sequence[str] = DEFAULT_ATCF_ENDPOINTS,
        timeout_s: float = 6.0,
        transport: Callable[[str, float], tuple[int, str]] | None = None,
    ) -> None:
        self.endpoints = tuple(endpoints)
        self.timeout_s = timeout_s
        self._transport = transport or _default_transport

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AtcfLiveSource(endpoints={len(self.endpoints)}, basin={TARGET_BASIN!r})"

    # --- internals ---------------------------------------------------------

    def _attempt(self, url: str) -> tuple[int, tuple[CycloneWaypoint, ...] | None, bool]:
        """One endpoint.

        Returns `(status, waypoints or None, spoke_atcf)`.

        The third element separates **the source is broken** from **the basin is
        quiet**, and getting it wrong misinforms a reader in the more flattering
        direction. A maintenance page returns 200 with text in it and no ATCF in
        it; calling that "no active storm" would tell someone their working feed
        has nothing to say when in fact it has stopped speaking.

        The discriminator is whether the body parses as ATCF *in any basin*. A
        valid Atlantic b-deck asked about the North Indian Ocean proves the
        source works and simply has nothing for us, which genuinely is "no
        active storm". A page of HTML proves nothing at all.
        """
        try:
            status, body = self._transport(url, self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - a raise is still "unavailable"
            log.warning("live source %s raised: %s", url, exc)
            return 0, None, False
        if status != 200:
            return status, None, False
        spoke_atcf = any(parse_atcf(body, basin=code) for code in ("AL", "WP", "EP", "CP", "IO"))
        # An empty body counts as the source having spoken: that is how a quiet
        # basin looks over HTTP, and treating silence as breakage would report
        # "live feed unavailable" whenever there is genuinely nothing to report.
        return status, (parse_atcf(body, basin=TARGET_BASIN) or None), spoke_atcf or not body.strip()

    def _record(self, waypoints: Sequence[CycloneWaypoint]) -> CycloneRecord:
        """Wrap fixes in a record. Only called when there are fixes to wrap."""
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        head = waypoints[0]
        storm_id = atcf_storm_id([TARGET_BASIN, head.iso_time[:4], head.nature or ""])
        return CycloneRecord(
            cyclone_id=f"{TARGET_BASIN}-live-{head.iso_time[:4]}",
            name=storm_id or f"{TARGET_BASIN} live",
            season=int(head.iso_time[:4]),
            basin=TARGET_BASIN,
            subbasin=None,
            source=self.identifier,
            observed=True,
            waypoints=tuple(waypoints),
            fetched_at=now,
            data_through=waypoints[-1].iso_time,
            peak_wind_kmph=None,
            limitation=ATCF_LIMITATION,
        )

    # --- public ------------------------------------------------------------

    async def probe(self) -> LiveStatus:
        """Try every endpoint and report what actually happened.

        The reason always carries the state's pinned promise —
        `live_unavailable_reason()` (feed down) or `no_active_storm_reason()`
        (feed answered, basin quiet) — with the per-endpoint statuses appended.
        A diagnostic alone ("tried 4 sources: 403, 403, 404, 200") tells a
        reader nothing about what is *not* being shown, which is the part that
        matters.
        """
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        observed: list[str] = []

        spoke = 0
        for url in self.endpoints:
            status, waypoints, spoke_atcf = self._attempt(url)
            observed.append(str(status))
            spoke += 1 if spoke_atcf else 0
            if waypoints:
                record = self._record(waypoints)
                head, tail = record.waypoints[0], record.waypoints[-1]
                return LiveStatus(
                    status="available",
                    source=url,
                    http_status=status,
                    reason=(
                        f"A live {TARGET_BASIN} storm is being tracked and its "
                        f"fixes are shown. Operational positions, not a forecast."
                    ),
                    checked_at=now,
                    endpoints=self.endpoints,
                    cyclone={
                        "cyclone_id": record.cyclone_id,
                        "name": record.name,
                        "season": record.season,
                        "basin": record.basin,
                        "waypoint_count": len(record.waypoints),
                        # RFC 3339, like `checked_at` directly above and like
                        # every timestamp `/track` serves. These used to be the
                        # at-rest `YYYY-MM-DD HH:MM:SS` spelling, so one response
                        # carried `checked_at: 2026-10-01T00:00:00Z` beside
                        # `last_timestamp: 2026-10-01 00:00:00` and disagreed
                        # with `/track` over a shared field name. The conversion
                        # is `base.iso_time_to_rfc3339`, the same call the track
                        # endpoints make.
                        "first_timestamp": iso_time_to_rfc3339(head.iso_time),
                        "last_timestamp": iso_time_to_rfc3339(tail.iso_time),
                        "latest_latitude": tail.latitude,
                        "latest_longitude": tail.longitude,
                        "latest_wind_kmph": tail.wind_kmph,
                        "latest_wind_reported": tail.wind_reported,
                        "data_through": iso_time_to_rfc3339(record.data_through),
                        "limitation": ATCF_LIMITATION,
                    },
                )
        # "No active storm" only when every source answered *in ATCF* and none
        # listed this basin. Anything else is a source we could not read.
        all_spoke = bool(self.endpoints) and spoke == len(self.endpoints)
        status = "no_active_storm" if all_spoke else "live_unavailable"
        detail = (
            "Every source answered in ATCF and none listed a storm for this basin."
            if all_spoke
            else "Sources tried, in order: "
            + ", ".join(f"{s} for {u.split('/')[2]}" for s, u in zip(observed, self.endpoints))
            + "."
        )
        # The promise must match the outcome: `live_unavailable_reason()`
        # claims the feed was unreachable, which is the one thing we know
        # is FALSE when every source answered and simply listed nothing.
        reason = (
            f"{no_active_storm_reason(now)} {detail}"
            if all_spoke
            else f"{live_unavailable_reason(now)} {detail}"
        )
        return LiveStatus(
            status=status,
            source=self.endpoints[0] if self.endpoints else "(none configured)",
            http_status=None if not observed else int(observed[-1]),
            reason=reason,
            checked_at=now,
            endpoints=self.endpoints,
        )

    async def fetch(self, cyclone_id: str | None = None) -> CycloneRecord | None:
        """The live storm, or `None` — **never a historical record**.

        `cyclone_id` is accepted to satisfy `CycloneSource` and ignored: this
        provider has at most one storm, and honouring an id here would invite
        someone to pass Remal's and get a `None` that looks like a lookup miss.
        """
        status = await self.probe()
        if status.status != "available":
            return None
        for url in self.endpoints:
            _, waypoints, _ = self._attempt(url)
            if waypoints:
                return self._record(waypoints)
        return None


__all__ = [
    "ATCF_LIMITATION",
    "DEFAULT_ATCF_ENDPOINTS",
    "TARGET_BASIN",
    "AtcfLiveSource",
    "TransportError",
    "LIVE_UNAVAILABLE_REASON",
]