"""Ensure the repo root is on sys.path so tests can import backend.*, and
clear the /advisory cache between tests so no test inherits another's state."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _clear_advisory_cache():
    """The /advisory endpoint caches fully validated responses. A test that
    succeeds populates the cache for its (cyclone, wind, origin) key, and a
    later test expecting a failure gets a cache hit instead of calling the
    mocked Gemini function. Clear before every test in the whole suite so
    each starts fresh."""
    from backend import main
    main._advisory_cache._store.clear()
    main._advisory_cache._order.clear()
    yield
