import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reposcout import static_files
from reposcout.static_files import ALLOWED, BUILD_COMMAND, add_routes, page, version

ROOT = Path(__file__).resolve().parents[1]
DIST = Path(static_files.__file__).parent / "static" / "dist"
FRONTEND_SRC = ROOT / "frontend" / "src"
IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def built():
    if not static_files.built():
        pytest.fail(f"The web UI isn't built. Run: {BUILD_COMMAND}")


@pytest.fixture
def client():
    app = FastAPI()
    add_routes(app)
    return TestClient(app)


def _asset_refs(html: str) -> list[str]:
    return re.findall(r'(?:src|href)="(/static/[^"]+)"', html)


def _head(html: str) -> str:
    return html[: html.index("</head>")]


def test_assets_served_with_immutable_cache(built, client):
    assert any(p.endswith(".css") for p in ALLOWED) and any(p.endswith(".js") for p in ALLOWED)
    for path in sorted(ALLOWED):
        resp = client.get(f"/static/{path}")
        assert resp.status_code == 200, path
        kind = "text/css" if path.endswith(".css") else "text/javascript"
        assert resp.headers["content-type"].startswith(kind), path
        assert resp.headers["cache-control"] == IMMUTABLE, path


@pytest.mark.parametrize("name", ["app.html", "index.html"])
def test_pages_link_only_served_assets(built, client, name):
    refs = _asset_refs(page(name))
    assert refs, name
    for ref in refs:
        assert ref.removeprefix("/static/") in ALLOWED, ref
        assert client.get(ref).status_code == 200, ref


@pytest.mark.parametrize(
    "path",
    [
        "../pyproject.toml",
        "assets/../../web/app.py",
        "app.html",
        "index.html",
        "assets",
        "assets/nope.js",
        "ui.css",
        "js/app.js",
        "%2e%2e/x",
        "/etc/passwd",
        "assets/%2e%2e/app.html",
    ],
)
def test_unknown_and_traversal_paths_404(client, path):
    assert client.get(f"/static/{path}").status_code == 404


def test_only_built_scripts_and_styles_are_allowed(built):
    assert ALLOWED and all(re.fullmatch(r"assets/[\w.-]+\.(js|css)", p) for p in ALLOWED)
    assert "app.html" not in ALLOWED and "index.html" not in ALLOWED


def test_version_is_short_hex(built):
    assert len(version()) == 12 and int(version(), 16) >= 0


def test_unknown_page_is_refused():
    with pytest.raises(ValueError):
        page("../pyproject.toml")


def test_missing_build_explains_how_to_build(monkeypatch, tmp_path):
    monkeypatch.setattr(static_files, "_DIST", tmp_path)
    for name in ("app.html", "index.html"):
        html = page(name)
        assert BUILD_COMMAND in html and "<script" not in html
    assert not static_files.built()


def _css(built_assets=ALLOWED) -> str:
    # Minified: no spaces after colons, lower-case hex colours, unquoted attribute values.
    css = "".join((DIST / p).read_text() for p in sorted(built_assets) if p.endswith(".css"))
    return re.sub(r"\s+", "", css).lower()


def test_stylesheet_defines_the_design_tokens(built):
    css = _css()
    for needle in (
        "--t-sm:14px",
        "--t-base:16px",
        "--t-lg:20px",
        "--t-xl:clamp(30px,4.2vw,44px)",
        "--blue:#0d59f2",
        "--ink:",
        "--panel:",
        "[data-theme=dark]",
        "prefers-reduced-motion",
        "pointer:coarse",
    ):
        assert needle in css, needle


def test_no_third_party_fonts(built):
    for path in [*ALLOWED, *static_files.PAGES]:
        text = (DIST / path).read_text(errors="ignore")
        assert "fonts.googleapis" not in text and "fonts.gstatic" not in text, path


def test_caddy_csp_has_no_inline_scripts():
    caddy = ROOT / "deploy" / "Caddyfile"
    line = next(ln for ln in caddy.read_text().splitlines() if "Content-Security-Policy" in ln)
    assert "script-src 'self';" in line
    assert "fonts.googleapis" not in line and "fonts.gstatic" not in line


@pytest.mark.parametrize(("name", "entry"), [("app.html", "app"), ("index.html", "index")])
def test_pages_have_no_inline_code(built, name, entry):
    html = page(name)
    assert "<style" not in html and "{{" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)
    assert not re.search(r" on[a-z]+=", html)
    assert re.search(
        rf'<script type="module" crossorigin src="/static/assets/{entry}-[\w-]+\.js"></script>', html
    )


@pytest.mark.parametrize("name", ["app.html", "index.html"])
def test_theme_is_set_before_first_paint(built, name):
    head = _head(page(name))
    match = re.search(r'<script src="/static/assets/theme-init-[\w-]+\.js"></script>', head)
    assert match
    # A classic (blocking) script, ahead of the stylesheet and the app.
    assert match.start() < head.index('rel="stylesheet"') and match.start() < head.index('type="module"')


def test_page_links_are_routes(built):
    # In a hash router any other fragment link (e.g. a skip link to "#view") would navigate home.
    for name in ("app.html", "index.html"):
        assert re.findall(r'href="#(?!/)[^"]*"', page(name)) == []
    for path in sorted(FRONTEND_SRC.rglob("*.tsx")):
        assert re.findall(r'href="#(?!/)[^"]*"', path.read_text()) == [], path.name


def test_frontend_source_attaches_events_as_handlers():
    sources = sorted(FRONTEND_SRC.rglob("*.ts*"))
    assert sources
    for path in sources:
        text = path.read_text()
        assert not re.search(r"""\son[a-z]+=["']""", text), path.name
        assert "<style" not in text and "<script" not in text, path.name
