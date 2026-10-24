"""The web UI's pages and shared stylesheet, used by both the local server and the public app."""

from __future__ import annotations

import hashlib
from importlib import resources

from fastapi.responses import Response


def _read(name: str) -> str:
    return resources.files("reposcout").joinpath(f"static/{name}").read_text(encoding="utf-8")


def page(name: str) -> str:
    """An HTML page with its stylesheet link pinned to the current stylesheet's content hash."""
    version = hashlib.sha256(_read("ui.css").encode()).hexdigest()[:12]
    return _read(name).replace("{{asset_version}}", version)


def stylesheet() -> Response:
    # The URL carries a content hash, so browsers can keep it until the stylesheet changes.
    return Response(
        _read("ui.css"),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
