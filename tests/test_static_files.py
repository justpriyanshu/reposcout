import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reposcout import static_files
from reposcout.static_files import ALLOWED, add_routes, page, version


@pytest.fixture
def client():
    app = FastAPI()
    add_routes(app)
    return TestClient(app)


def test_asset_served_with_immutable_cache(client):
    resp = client.get(f"/static/{version()}/ui.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_js_served_as_javascript(client):
    scripts = [p for p in sorted(ALLOWED) if p.endswith(".js")]
    if not scripts:
        pytest.skip("no JS modules yet")
    for path in scripts:
        resp = client.get(f"/static/{version()}/{path}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/javascript")


def test_old_version_still_served_without_cache(client):
    resp = client.get("/static/000000000000/ui.css")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path",
    ["../pyproject.toml", "js/../../web/app.py", "app.html", "nope.css", "%2e%2e/x", "/etc/passwd"],
)
def test_unknown_and_traversal_paths_404(client, path):
    assert client.get(f"/static/{version()}/{path}").status_code == 404


def test_version_is_short_hex():
    assert len(version()) == 12 and int(version(), 16) >= 0
    assert "ui.css" in ALLOWED and "app.html" not in ALLOWED


def test_page_inserts_static_prefix():
    html = page("app.html")
    assert f"/static/{version()}/" in html and "{{" not in html


def test_ui_css_alias(client):
    resp = client.get("/ui.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")
    assert static_files.version()


def _css():
    return (Path(static_files.__file__).parent / "static" / "ui.css").read_text()


def test_stylesheet_defines_the_design_tokens():
    css = _css()
    for needle in (
        "--t-sm: 14px",
        "--t-base: 16px",
        "--t-lg: 20px",
        "--t-xl: clamp(30px, 4.2vw, 44px)",
        "--blue: #0D59F2",
        "--ink:",
        "--panel:",
        '[data-theme="dark"]',
        "prefers-reduced-motion",
        "pointer: coarse",
    ):
        assert needle in css, needle


def test_no_third_party_fonts():
    static_dir = Path(static_files.__file__).parent / "static"
    for name in ALLOWED:
        text = (static_dir / name).read_text(errors="ignore")
        assert "fonts.googleapis" not in text and "fonts.gstatic" not in text, name
    assert "fonts.googleapis" not in _css()


def test_caddy_csp_has_no_inline_scripts():
    caddy = Path(__file__).resolve().parents[1] / "deploy" / "Caddyfile"
    line = next(ln for ln in caddy.read_text().splitlines() if "Content-Security-Policy" in ln)
    assert "script-src 'self';" in line
    assert "fonts.googleapis" not in line and "fonts.gstatic" not in line


def test_public_page_has_no_inline_code():
    html = page("app.html")
    assert "<style" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)
    assert not re.search(r" on[a-z]+=", html)
    assert f"/static/{version()}/js/app.js" in html


def test_js_modules_attach_events_with_listeners():
    js_dir = Path(static_files.__file__).parent / "static" / "js"
    for path in sorted(js_dir.glob("*.js")):
        text = path.read_text()
        assert not re.search(r"""\son[a-z]+=["']""", text), path.name
        assert "<style" not in text and "<script" not in text, path.name


def test_page_links_are_routes():
    # In a hash router any other fragment link (e.g. a skip link to "#view") would navigate home.
    html = page("app.html")
    assert re.findall(r'href="#(?!/)[^"]*"', html) == []


def test_theme_is_set_before_first_paint():
    html = page("app.html")
    head = html[: html.index("</head>")]
    assert re.search(r'<script src="[^"]*/js/theme-init\.js"></script>', head)
    assert head.index("theme-init.js") < head.index("ui.css")
