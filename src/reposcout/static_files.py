"""The web UI's pages and static assets, used by both the local server and the public app."""

from __future__ import annotations

import hashlib
from functools import cache
from importlib import resources

from fastapi import FastAPI
from fastapi.responses import Response

_ROOT = resources.files("reposcout").joinpath("static")
_IMMUTABLE = "public, max-age=31536000, immutable"
_MEDIA_TYPES = {".css": "text/css", ".js": "text/javascript"}


def _read(name: str) -> str:
    return _ROOT.joinpath(name).read_text(encoding="utf-8")


def _allowed() -> frozenset[str]:
    names = {"ui.css"}
    js = _ROOT.joinpath("js")
    if js.is_dir():
        names.update(f"js/{f.name}" for f in js.iterdir() if f.name.endswith(".js"))
    return frozenset(names)


ALLOWED: frozenset[str] = _allowed()


@cache
def version() -> str:
    """A short content hash over every served asset, so URLs change exactly when an asset does."""
    digest = hashlib.sha256()
    for name in sorted(ALLOWED):
        digest.update(name.encode() + b"\0" + _ROOT.joinpath(name).read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def page(name: str) -> str:
    """An HTML page with its asset links pinned to the current asset version."""
    html = _read(name).replace("/ui.css?v={{asset_version}}", "{{static}}/ui.css")
    return html.replace("{{static}}", f"/static/{version()}")


def _asset(path: str, immutable: bool) -> Response:
    # Lookup is by exact membership in ALLOWED; user input is never joined into a path.
    if path not in ALLOWED:
        return Response(status_code=404)
    suffix = path[path.rindex(".") :]
    return Response(
        _ROOT.joinpath(path).read_bytes(),
        media_type=_MEDIA_TYPES[suffix],
        headers={"Cache-Control": _IMMUTABLE if immutable else "no-cache"},
    )


def add_routes(app: FastAPI) -> None:
    """Serve the allow-listed assets at /static/{version}/..., plus the legacy /ui.css alias."""

    @app.get("/static/{ver}/{path:path}", include_in_schema=False)
    def static_asset(ver: str, path: str) -> Response:
        # An old version still gets the current file (a page open during a deploy keeps working).
        return _asset(path, immutable=ver == version())

    @app.get("/ui.css", include_in_schema=False)
    def ui_css() -> Response:
        return _asset("ui.css", immutable=False)
