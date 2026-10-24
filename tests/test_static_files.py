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
