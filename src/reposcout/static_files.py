"""The web UI's pages and static assets, used by both the local server and the public app.

The UI is a React app in ``frontend/``; ``npm --prefix frontend run build`` writes it to
``static/dist``: the two pages (``app.html`` for the public app, ``index.html`` for the local
page) and their scripts and stylesheets under ``assets/``, each with a content hash in its file
name. The pages link the assets as ``/static/assets/<name>``, so an asset's URL changes exactly
when its content does and every asset can be cached forever.
"""

from __future__ import annotations

import hashlib
from functools import cache
from importlib import resources

from fastapi import FastAPI
from fastapi.responses import Response

_DIST = resources.files("reposcout").joinpath("static").joinpath("dist")
_IMMUTABLE = "public, max-age=31536000, immutable"
_MEDIA_TYPES = {".css": "text/css", ".js": "text/javascript"}
PAGES = frozenset({"app.html", "index.html"})

BUILD_COMMAND = "npm --prefix frontend ci && npm --prefix frontend run build"
_NOT_BUILT = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RepoScout: the web UI isn't built</title></head>
<body>
<h1>RepoScout: the web UI isn't built yet</h1>
<p>The API is running, but this install has no built web UI. From the repository root, run:</p>
<pre>{BUILD_COMMAND}</pre>
<p>then restart the server. Installed packages and the Docker image ship the built UI.</p>
</body>
</html>
"""


def _allowed() -> frozenset[str]:
    """Every servable asset, as "assets/<file name>": the build's scripts and stylesheets only."""
    assets = _DIST.joinpath("assets")
    if not assets.is_dir():
        return frozenset()
    return frozenset(
        f"assets/{f.name}"
        for f in assets.iterdir()
        if f.is_file() and f.name.endswith(tuple(_MEDIA_TYPES)) and not f.name.startswith(".")
    )


ALLOWED: frozenset[str] = _allowed()


def built() -> bool:
    """True when the web UI has been built (both pages exist)."""
    return all(_DIST.joinpath(name).is_file() for name in PAGES)


@cache
def version() -> str:
    """A short content hash over the built pages and assets ("unbuilt" when there is no build)."""
    if not built():
        return "unbuilt"
    digest = hashlib.sha256()
    for name in sorted(PAGES | ALLOWED):
        digest.update(name.encode() + b"\0" + _DIST.joinpath(name).read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def page(name: str) -> str:
    """A built HTML page ("app.html" or "index.html"), or a page explaining how to build the UI."""
    if name not in PAGES:
        raise ValueError(f"unknown page: {name}")
    built_page = _DIST.joinpath(name)
    if not built_page.is_file():
        return _NOT_BUILT
    return built_page.read_text(encoding="utf-8")


def _asset(path: str) -> Response:
    # Lookup is by exact membership in ALLOWED; user input is never joined into a path.
    if path not in ALLOWED:
        return Response(status_code=404)
    suffix = path[path.rindex(".") :]
    return Response(
        _DIST.joinpath(*path.split("/")).read_bytes(),
        media_type=_MEDIA_TYPES[suffix],
        headers={"Cache-Control": _IMMUTABLE},
    )


def add_routes(app: FastAPI) -> None:
    """Serve the built assets at /static/assets/<name>, cached forever (their names are content hashes)."""

    @app.get("/static/{path:path}", include_in_schema=False)
    def static_asset(path: str) -> Response:
        return _asset(path)
