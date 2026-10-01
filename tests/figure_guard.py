"""Shared figure guard: pin every number written in prose.

Six measured figures were wrong across three rounds of one task because an agent
hand-counted something, wrote it in a docstring, and then graded its own work
(MEMORY.md §45). Self-audit by the same agent that produced the number is not
evidence. The only evidence is a test that *recomputes* the figure from the
source data and fails when the prose drifts.

`prose_figures()` finds every 3+-character digit-run in a module's docstrings and
comments. Code string literals are excluded, which is what stops the check from
matching its own allowlist and its own assertion messages — and what stops a
figure under test from counting as "pinned" merely by being mentioned.

Each consuming module keeps its own allowlist, because what counts as a
definition (a unit conversion, a year) differs per file.

`test_paths` accepts several files because a figure can legitimately be pinned
anywhere in the suite — a claim about the IBTrACS archive is recomputed in the
IBTrACS tests, not in the ML module's. What matters is that *a* test recomputes
it, not which file that test lives in.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

#: 3+ characters, so `2` and `42` are not mistaken for measurements, but `299`,
#: `21.9` and `1.852` are. Thousands separators are tolerated.
FIGURE_PATTERN = re.compile(r"\d[\d,]{2,}\.?\d*")


def prose_figures(path: Path) -> list[tuple[str, int]]:
    """Every 3+-character digit-run in `path`'s docstrings and comments.

    Docstrings are located with `ast` so a figure inside one is found without
    parsing prose; comments with `tokenize`, which sees them without
    interpreting them. Returns `(figure, line)` pairs, figures as written.
    """
    source = path.read_text()
    spans: list[tuple[int, int]] = []

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        raw = node.body[0].value if node.body else None
        if isinstance(raw, ast.Constant) and isinstance(raw.value, str):
            line = getattr(raw, "lineno", 1)
            spans.append((line, line + raw.value.count("\n")))

    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            spans.append((tok.start[0], tok.end[0]))

    figures: list[tuple[str, int]] = []
    for index, line in enumerate(source.splitlines(), start=1):
        if not any(lo <= index <= hi for lo, hi in spans):
            continue
        for run in FIGURE_PATTERN.findall(line):
            figures.append((run.rstrip("."), index))
    return figures


def unpinned_figures(
    module_path: Path,
    test_paths: Path | list[Path] | tuple[Path, ...],
    allowlist: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Split into `(unpinned, unjustified)`.

    A figure is acceptable if a test recomputes it and asserts the literal, or if
    it is allowlisted **with a reason**. An allowlist entry with no reason is
    reported separately rather than waved through, because an unreasoned
    allowlist is exactly where hand-counted numbers go to hide.
    """
    if isinstance(test_paths, Path):
        test_paths = [test_paths]
    test_source = "\n".join(path.read_text() for path in test_paths)
    unpinned: list[str] = []
    unjustified: list[str] = []

    for figure, line in prose_figures(module_path):
        bare = figure.replace(",", "")
        if bare in allowlist:
            if not allowlist[bare]:
                unjustified.append(f"{bare} (line {line}) — allowlisted with no reason")
            continue
        # A year is a definition, not a measurement of this file.
        if len(bare) == 4 and bare.isdigit() and 1900 <= int(bare) <= 2100:
            continue
        if bare in test_source or figure in test_source:
            continue
        unpinned.append(f"{figure} at {module_path.name}:{line}")

    return unpinned, unjustified


def assert_figures_pinned(
    module_path: Path,
    test_paths: Path | list[Path] | tuple[Path, ...],
    allowlist: dict[str, str],
) -> None:
    unpinned, unjustified = unpinned_figures(module_path, test_paths, allowlist)
    assert not unjustified, (
        "allowlist entries need a reason, or they are where hand-counted numbers "
        f"go to hide: {'; '.join(unjustified)}"
    )
    assert not unpinned, (
        "these figures are in prose and no test recomputes them, so the suite "
        "cannot catch them going stale. Either pin one by recomputing it from the "
        "source data and asserting the literal in the test file, or delete it from "
        "the docstring. To allowlist a figure that is not a measurement, add it to "
        f"the allowlist with a reason: {'; '.join(unpinned)}"
    )