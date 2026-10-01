"""Build `data/cyclones/catalogue.json` from the committed IBTrACS archive.

    python -m backend.data_pipeline.ingest_ibtracs_ni --out data/cyclones/catalogue.json

This is the only thing in the repository that turns the raw 27 MB best-track
file into something the API can serve, and it is a build step, not a service.
It is checked in because the alternative is worse: the app would either parse
27 MB of CSV on the first request after a cold start, or ship without a
historical cyclone list at all.

Three properties this script exists to guarantee, in the order they were nearly
got wrong:

**No network.** The input is committed. `fetch_ibtracs.py`, its predecessor,
downloads from NOAA — which is right for populating the repo once and wrong for
regenerating a checked-in artefact, because a build that can fail on someone
else's uptime is a build whose output nobody can reproduce. This one reads a
file and, if that file is missing, says so and exits non-zero rather than
quietly writing a catalogue of nothing.

**Deterministic bytes.** The output is committed, so a diff in it has to mean
something. Every source of run-to-run variation is pinned: no wall-clock
timestamp, no set iteration order (records are sorted by season and id, and
`json.dump` is called with `sort_keys=True`), no unsorted dict comprehension
output, and the float formatting is fixed by `round(..., 1)` inside the parser
rather than by luck. `generated_at` is the *input file's mtime* — see
`backend.cyclones/historical.py::_source_read_time` for why, and for the one
cost that carries (git does not preserve mtimes, so a fresh clone's
regeneration shows a one-line diff in `generated_at`; `--generated-at` pins it).

**The basin filter is not optional.** The input is the North Indian Ocean
*list*, which also contains 4,525 Western Pacific and 482 North Atlantic rows.
`--basin` exists on this CLI only so that the default is visible in `--help`
and cannot be quietly widened; the only value that works is `NI`, and anything
else exits non-zero. A flag that accepted `WP` would be a way for the next
person to publish a Philippine cyclone in a Sundarbans flood map, and a flag
that silently ignored an unsupported value would be worse.

The count and the path are printed on success, because a build step that says
nothing is a build step nobody checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.cyclones.base import CycloneSource
from backend.cyclones.historical import (
    DEFAULT_IBTRACS_PATH,
    IBTRACS_SOURCE_ID,
    NI_BASIN_CODE,
    REMAL_CYCLONE_ID,
    IbtracsSource,
    build_catalogue,
)

#: Where the committed catalogue lives. Repo-relative, resolved against
#: `REPO_ROOT` rather than the working directory, so the script behaves the same
#: whether it is run from the root, from `backend/`, or from a CI runner that
#: happens to `cd` somewhere else first. An output path that lands in the
#: wrong place is the failure this prevents — and it would be a *committed*
#: file in the wrong place.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_PATH = REPO_ROOT / "data" / "cyclones" / "catalogue.json"

#: The only basin this ingestion supports, and the reason `--basin` takes a
#: choice at all. See the module docstring.
SUPPORTED_BASIN = NI_BASIN_CODE


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface. Three optional flags and no positional arguments.

    `--in` is spelled with a trailing `n` rather than `--input` because a
    capital `I` in `--Input` has been misread as an `l` by enough tools to be
    worth avoiding; `--in` is unambiguous next to the `--out` it pairs with.
    """
    parser = argparse.ArgumentParser(
        prog="python -m backend.data_pipeline.ingest_ibtracs_ni",
        description=(
            "Stream the committed IBTrACS CSV and write the NI cyclone catalogue. "
            "Takes no network action; the input is a file in this repository."
        ),
    )
    parser.add_argument(
        "--in",
        dest="source",
        type=Path,
        default=DEFAULT_IBTRACS_PATH,
        help=(
            f"IBTrACS CSV to read (default: {DEFAULT_IBTRACS_PATH.name}, "
            f"committed at the repo root)"
        ),
    )
    parser.add_argument(
        "--out",
        dest="out",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help=f"catalogue to write (default: {DEFAULT_OUT_PATH.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=1970,
        help=(
            "earliest SEASON to include. A default argument, not a shared "
            "constant: T6 applies its own SEASON filter when it builds the ML "
            "training set, and the two must be able to move independently"
        ),
    )
    parser.add_argument(
        "--basin",
        choices=[SUPPORTED_BASIN],
        default=SUPPORTED_BASIN,
        help=(
            f"basin to keep; only {SUPPORTED_BASIN} is supported. The input file "
            f"also contains WP and NA rows and they must never reach a record"
        ),
    )
    parser.add_argument(
        "--generated-at",
        default=None,
        help=(
            "pin the catalogue's generated_at / fetched_at timestamp instead of "
            "taking it from the input file's mtime. Format: 2026-10-01T00:00:00Z"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the catalogue. Returns a process exit code rather than calling
    `sys.exit`, so it is callable from a test or another script.

    Returns 1 with a message on `stderr` for every failure — a missing input, an
    empty result, a storm that failed to parse — rather than writing a file
    that is wrong. A build step that produces a plausible-looking artefact from
    a broken input is worse than one that stops.
    """
    args = build_parser().parse_args(argv)

    source_path: Path = args.source
    if not source_path.is_file():
        print(
            f"error: IBTrACS input not found: {source_path}\n"
            f"       The archive is committed at the repo root as "
            f"{DEFAULT_IBTRACS_PATH.name}. This script does not download it — "
            f"see its module docstring.",
            file=sys.stderr,
        )
        return 1

    catalogue = build_catalogue(path=source_path, since=args.since, fetched_at=args.generated_at)
    cyclones = catalogue["cyclones"]
    if not cyclones:
        print(
            f"error: no {SUPPORTED_BASIN} storms at season >= {args.since} in "
            f"{source_path}. Refusing to write an empty catalogue.",
            file=sys.stderr,
        )
        return 1

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as handle:
        # `sort_keys` and a fixed indent are the determinism guarantee; the
        # trailing newline keeps `git diff` from reporting "\ No newline at end
        # of file" on every record.
        json.dump(catalogue, handle, indent=2, sort_keys=True)
        handle.write("\n")

    remal = next((c for c in cyclones if c["cyclone_id"] == REMAL_CYCLONE_ID), None)
    print(
        f"source  {catalogue['generated_from']} "
        f"(basin {SUPPORTED_BASIN}, season >= {args.since})"
    )
    print(f"wrote   {out_path} — {len(cyclones)} cyclones, {catalogue['generated_at']}")
    if remal is not None:
        print(
            f"        includes {REMAL_CYCLONE_ID} ({remal['name']} {remal['season']}): "
            f"{len(remal['waypoints'])} fixes, "
            f"peak {remal['peak_wind_kmph']} kmph"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
