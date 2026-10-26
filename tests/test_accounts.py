"""Each person sees only their own activity, and can sign in with GitHub."""

import json
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from reposcout.embeddings import HashingEmbedder
from reposcout.web.accounts import SESSION_COOKIE, VISITOR_COOKIE, safe_next
from reposcout.web.app import create_public_app
from reposcout.web.links import RepoId

from .test_web import KEY, FakeGitHub, FakeGroq, make_workspace, public_settings  # noqa: F401

GITHUB_USER = {
    "id": 4242,
    "login": "octocat",
    "name": "The Octocat",
    "avatar_url": "https://avatars.githubusercontent.com/u/4242",
}


class FakeGitHubWithUsers(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.user_repo_calls = 0

    def user_repos(self, login):
        self.user_repo_calls += 1
        return [
            {"full_name": f"{login}/hello", "description": "Hi", "language": "Python", "stargazers_count": 3,
             "pushed_at": "2026-10-01T00:00:00Z", "html_url": f"https://github.com/{login}/hello", "fork": False},
        ]  # fmt: skip


class FakeOAuthAndGroq(FakeGroq):
    """GitHub's OAuth endpoints plus the chat provider, behind one mock transport."""

    def __init__(self):
        super().__init__()
        self.token_requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com" and request.url.path == "/login/oauth/access_token":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            if form.get("code") != ["good-code"]:
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": "gho_secret", "token_type": "bearer"})
        if request.url.host == "api.github.com" and request.url.path == "/user":
            assert request.headers["authorization"] == "Bearer gho_secret"
            return httpx.Response(200, json=GITHUB_USER)
        return super().handler(request)


@pytest.fixture
def site(public_settings, sample_repo):  # noqa: F811
    settings = replace(
        public_settings,
        github_client_id="Iv1.test",
        github_client_secret="shh",
        public_url="http://testserver",
    )
    fake = FakeOAuthAndGroq()
    github = FakeGitHubWithUsers()
    ws = make_workspace(settings, sample_repo)
    app = create_public_app(
        settings,
        ws,
        HashingEmbedder(),
        github,
        http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )

    def browser() -> TestClient:
        return TestClient(app, follow_redirects=False)

    return browser, ws, fake, github


def add(client, url="octo/shop"):
    resp = client.post("/api/repos", json={"url": url})
    assert resp.status_code == 202, resp.text
    return resp.json()["repo"]


def sign_in(client, fake, next_path="/#/ask/octo/shop"):
    start = client.get("/auth/github/login", params={"next": next_path})
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    callback = client.get("/auth/github/callback", params={"code": "good-code", "state": query["state"][0]})
    return start, callback


def test_people_only_see_their_own_repositories(site):
    browser, ws, _, _ = site
    alice, bob = browser(), browser()
    add(alice)
    ws.drain()
    assert [r["full_name"] for r in alice.get("/api/repos").json()["repos"]] == ["octo/shop"]
    # Bob can't list, probe, search or ask about Alice's repository.
    assert bob.get("/api/repos").json()["repos"] == []
    assert bob.get("/api/repos/octo/shop").status_code == 404
    assert bob.post("/api/repos/octo/shop/search", json={"query": "token"}).json()["code"] == "not_indexed"
    ask = bob.post("/api/repos/octo/shop/ask", json={"question": "how are tokens checked?"}, headers=KEY)
    assert ask.status_code == 404
    # Once Bob adds it himself, the shared index makes it ready straight away.
    assert add(bob)["ready"] is True
    assert bob.post("/api/repos/octo/shop/search", json={"query": "token"}).status_code == 200


def test_removing_a_repository_only_touches_your_list(site):
    browser, ws, _, _ = site
    alice, bob = browser(), browser()
    add(alice), add(bob)
    ws.drain()
    assert alice.delete("/api/repos/octo/shop").json() == {"ok": True}
    assert alice.get("/api/repos").json()["repos"] == []
    assert len(bob.get("/api/repos").json()["repos"]) == 1


def test_an_evicted_index_shows_as_expired_and_can_be_added_again(site):
    browser, ws, _, _ = site
    alice = browser()
    add(alice)
    ws.drain()
    ws._delete(RepoId("octo", "shop").key)
    (record,) = alice.get("/api/repos").json()["repos"]
    assert record["status"] == "expired" and record["ready"] is False and record["full_name"] == "octo/shop"
    assert alice.get("/api/repos/octo/shop").json()["repo"]["status"] == "expired"
    add(alice)
    ws.drain()
    assert alice.get("/api/repos/octo/shop").json()["repo"]["ready"] is True


def test_review_uses_repository_context_only_from_your_list(site):
    browser, ws, _, _ = site
    alice, bob = browser(), browser()
    add(alice)
    ws.drain()
    url = {"url": "https://github.com/octo/shop/pull/5"}
    assert alice.post("/api/review", json=url, headers=KEY).json()["used_repo_context"] is True
    assert bob.post("/api/review", json=url, headers=KEY).json()["used_repo_context"] is False


def test_anonymous_visitors_get_a_private_cookie(site):
    browser, _, _, _ = site
    client = browser()
    me = client.get("/api/me")
    assert me.json() == {"user": None, "github_login": True}
    cookie = me.headers["set-cookie"]
    assert cookie.startswith(f"{VISITOR_COOKIE}=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
    # The same browser keeps its id (no new cookie on the next request).
    assert "set-cookie" not in client.get("/api/me").headers


def test_sign_in_with_github_merges_the_anonymous_list(site):
    browser, ws, fake, _ = site
    client = browser()
    add(client)
    ws.drain()
    start, callback = sign_in(client, fake)
    authorize = urlparse(start.headers["location"])
    query = parse_qs(authorize.query)
    assert authorize.netloc == "github.com" and authorize.path == "/login/oauth/authorize"
    assert query["client_id"] == ["Iv1.test"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://testserver/auth/github/callback"]
    assert "scope" not in query or query["scope"] == [""]  # public profile only
    # PKCE: the verifier sent to GitHub matches the challenge sent at the start.
    import base64
    import hashlib

    verifier = fake.token_requests[0]["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [challenge]

    assert callback.status_code == 302 and callback.headers["location"] == "/#/ask/octo/shop"
    assert f"{SESSION_COOKIE}=" in callback.headers["set-cookie"]
    me = client.get("/api/me").json()
    assert me["user"]["login"] == "octocat" and me["user"]["html_url"] == "https://github.com/octocat"
    assert len(client.get("/api/repos").json()["repos"]) == 1  # the anonymous list moved over
    assert "gho_secret" not in json.dumps(me)

    # Another browser signing in to the same account sees the same list.
    laptop = browser()
    sign_in(laptop, fake)
    assert len(laptop.get("/api/repos").json()["repos"]) == 1

    # Signing out forgets the session and starts a new anonymous list.
    assert client.post("/api/logout", json={}).json() == {"ok": True}
    client.cookies.clear()
    assert client.get("/api/me").json()["user"] is None
    assert client.get("/api/repos").json()["repos"] == []


def test_sign_in_failures_redirect_with_a_message(site):
    browser, _, fake, _ = site
    client = browser()
    client.get("/auth/github/login")
    wrong_state = client.get("/auth/github/callback", params={"code": "good-code", "state": "forged"})
    assert "login_error=" in wrong_state.headers["location"]
    start = client.get("/auth/github/login")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    bad_code = client.get("/auth/github/callback", params={"code": "nope", "state": state})
    assert "login_error=" in bad_code.headers["location"]
    cancelled = client.get("/auth/github/callback", params={"error": "access_denied"})
    assert "cancelled" in parse_qs(urlparse(cancelled.headers["location"]).query)["login_error"][0]
    assert client.get("/api/me").json()["user"] is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/#/ask/a/b", "/#/ask/a/b"),
        ("//evil.example/x", "/"),
        ("https://evil.example", "/"),
        ("/\\evil.example", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_after_sign_in_only_same_site_paths_are_allowed(value, expected):
    assert safe_next(value) == expected


def test_sign_in_is_hidden_without_an_oauth_app(public_settings, sample_repo):  # noqa: F811
    ws = make_workspace(public_settings, sample_repo)
    app = create_public_app(public_settings, ws, HashingEmbedder(), FakeGitHub())
    client = TestClient(app, follow_redirects=False)
    assert client.get("/api/me").json()["github_login"] is False
    assert client.get("/auth/github/login").status_code == 404


def test_your_github_repositories(site):
    browser, _, fake, github = site
    client = browser()
    assert client.get("/api/me/github-repos").json()["code"] == "sign_in"
    sign_in(client, fake)
    repos = client.get("/api/me/github-repos").json()["repos"]
    assert repos == [
        {"full_name": "octocat/hello", "description": "Hi", "language": "Python", "stars": 3,
         "pushed_at": "2026-10-01T00:00:00Z", "html_url": "https://github.com/octocat/hello", "fork": False},
    ]  # fmt: skip
    client.get("/api/me/github-repos")
    assert github.user_repo_calls == 1  # cached for a few minutes


def test_cross_site_writes_are_refused_and_pages_get_security_headers(site):
    browser, _, _, _ = site
    client = browser()
    evil = client.post("/api/repos", json={"url": "octo/shop"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["code"] == "bad_origin"
    same = client.post("/api/repos", json={"url": "octo/shop"}, headers={"Origin": "http://testserver"})
    assert same.status_code == 202
    headers = client.get("/api/me").headers
    assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"
    assert "https://avatars.githubusercontent.com" in headers["content-security-policy"]
