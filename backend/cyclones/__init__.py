"""Cyclone data — one normalized record, from any source.

    base  — CycloneRecord, CycloneWaypoint, CycloneSource, LiveStatus and the
            no-substitution sentence. Pure vocabulary: no I/O, no network.

The two implementations that produce records land here as later steps of the
same plan:

    historical  — IbtracsSource, reading the committed NI-basin IBTrACS CSV
    live        — AtcfLiveSource, probing public ATCF bulletins

and two consumers sit above them:

    registry  — id -> record, the catalogue, the live probe (re-exported here
                once it exists; until then importing it here would make every
                `backend.cyclones` import fail on a module that is not written)
    scenarios — the scenario catalogue, each one resolving to a wind

`base` is re-exported for convenience so a caller can reach the vocabulary
without knowing which file it lives in, and so that the package has one import
path even as the module list grows.
"""

from .base import (
    LIVE_UNAVAILABLE_REASON,
    CycloneRecord,
    CycloneSource,
    CycloneWaypoint,
    LiveStatus,
    live_unavailable_reason,
    peak_wind_kmph,
)

__all__ = [
    "LIVE_UNAVAILABLE_REASON",
    "CycloneRecord",
    "CycloneSource",
    "CycloneWaypoint",
    "LiveStatus",
    "live_unavailable_reason",
    "peak_wind_kmph",
]
