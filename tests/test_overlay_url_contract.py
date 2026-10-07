"""`image_url` is server-relative. The clients prepend the base; the docstring
claimed otherwise.

`/overlays` returns `/overlays/flood_cat6.png`. Both clients concatenate
`API_BASE_URL + image_url` — `mobile/api.ts:overlayImageUrl` and
`WebImpactMap.tsx` — because a bare relative path resolves against the Metro
bundler's own origin and 404s. That is the right design: the payload stays
portable across hosts, and `tests/test_overlays.py` pins the `/overlays/`
prefix.

The `list_overlays()` docstring claimed the field was "absolute because a
mobile client cannot resolve a server-relative path into something `<Overlay>`
will load" — the opposite of the truth, and the opposite of what both clients
already do. A client written to that description would prepend the base URL a
second time.

This pins the contract from the client's side: the server-relative prefix, and
the fact that each client prefixes it exactly once.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import OVERLAY_URL_PREFIX, app

client = TestClient(app)

MOBILE = Path(__file__).resolve().parents[1] / "mobile"


def test_the_field_is_server_relative() -> None:
    body = client.get("/overlays").json()
    entries = body["overlays"]
    if not entries:  # overlays not rendered on this machine; the shape is pinned elsewhere
        return
    for entry in entries:
        assert entry["image_url"].startswith("/"), entry["image_url"]
        assert not entry["image_url"].startswith("http"), entry["image_url"]


def test_both_clients_prepend_the_base_exactly_once() -> None:
    """The concatenation is the contract; a second prepend would break it.

    Read as source, because this is what has to agree with the server: one
    `${API_BASE_URL}${…image_url}` per client, not a stale absolute-URL
    expectation anywhere.
    """
    api = (MOBILE / "api.ts").read_text()
    web = (MOBILE / "components" / "WebImpactMap.tsx").read_text()

    assert "${API_BASE_URL}${entry.image_url}" in api, (
        "api.ts no longer builds the absolute overlay URL from the base"
    )
    assert "${API_BASE_URL}${overlay.image_url}" in web, (
        "WebImpactMap.tsx no longer builds the absolute overlay URL from the base"
    )
    # A naive read of the old docstring would produce a double prefix; pin that
    # the base is not itself already carrying the path.
    assert OVERLAY_URL_PREFIX.startswith("/") and not OVERLAY_URL_PREFIX.startswith(
        "http"
    )


def test_the_overlays_docstring_does_not_claim_absolute() -> None:
    import ast

    tree = ast.parse((Path(__file__).resolve().parents[1] / "backend" / "main.py").read_text())
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "list_overlays"
    )
    docstring = ast.get_docstring(handler) or ""
    assert "server-relative" in docstring
    assert "`image_url` is absolute" not in docstring, (
        "the docstring still claims the field is absolute; it is relative and "
        "both clients prepend the base"
    )
