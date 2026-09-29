"""
Vercel's entrypoint. Added 2026-09-29.

Vercel auto-detects a Python function by looking for `app.py` at the project
root and importing the `app` object it exposes. The real application lives in
`backend/main.py`, which Vercel would not find on its own. This file exists
only to satisfy that lookup, and it deliberately contains nothing else — no
configuration, no middleware, no second definition of anything.

Why there is no pyproject.toml declaring dependencies alongside requirements.txt:
two files that can disagree about the same dependency list is precisely the
failure mode this repo already rejected when it pinned the versions (see the
header of requirements.txt). One list, one owner.

Local and Render are unaffected — both still run `uvicorn backend.main:app`,
which is the same object this re-exports.
"""

from backend.main import app

__all__ = ["app"]
