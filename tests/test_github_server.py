import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from reposcout import static_files
from reposcout.github import GitHubClient, GitHubError, parse_pr_ref, verify_signature
from reposcout.review import Reviewer
from reposcout.server import create_app
from reposcout.services import build_services

from .conftest import FakeLLM, findings_reply

PR_DIFF = """diff --git a/auth/session.py b/auth/session.py
--- a/auth/session.py
+++ b/auth/session.py
@@ -9,2 +9,3 @@ def verify_token(secret: str, token: str, signature: str) -> bool:
 def logout(session):
     session.clear()
+    print(session.user.password)
"""


class FakeGitHub:
    """Records the GitHub API calls the server makes."""

    def __init__(self, reject_inline=False):
        self.reject_inline = reject_inline
        self.posted: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.headers["Accept"] == "application/vnd.github.diff":
            return httpx.Response(200, text=PR_DIFF)
        if request.method == "GET":
            return httpx.Response(200, json={"title": "Log user", "body": "", "head": {"sha": "abc123"}})
        body = json.loads(request.content)
        self.posted.append(body)
        if self.reject_inline and body.get("comments"):
            return httpx.Response(422, json={"message": "Line could not be resolved"})
        return httpx.Response(200, json={"id": 1, "html_url": "https://github.com/o/r/pull/7#review"})

    def client(self) -> GitHubClient:
        return GitHubClient("token", client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ github helpers


@pytest.mark.parametrize("ref", ["octo/shop#7", "https://github.com/octo/shop/pull/7", "octo/shop/pulls/7"])
def test_parse_pr_ref(ref):
    pr = parse_pr_ref(ref)
    assert (pr.owner, pr.repo, pr.number) == ("octo", "shop", 7)


def test_parse_pr_ref_rejects_garbage():
    with pytest.raises(GitHubError):
        parse_pr_ref("not a pr")


def test_verify_signature():
    body = b'{"a":1}'
    assert verify_signature("s3cret", body, sign("s3cret", body))
    assert not verify_signature("s3cret", body, sign("other", body))
    assert not verify_signature("s3cret", body, None)
    assert not verify_signature("", body, sign("", body))


def test_post_review_inline_comments():
    gh = FakeGitHub()
    result = Reviewer(
        FakeLLM(
            findings_reply(
                [
                    {
                        "line": 11,
                        "severity": "critical",
                        "category": "security",
                        "title": "Password logged",
                        "message": "Never log passwords.",
                    }
                ]
            )
        )
    ).review(PR_DIFF)
    gh.client().post_review(parse_pr_ref("o/r#7"), "abc123", result)
    (payload,) = gh.posted
    assert payload["commit_id"] == "abc123" and payload["event"] == "COMMENT"
    assert payload["comments"][0]["path"] == "auth/session.py"
    assert payload["comments"][0]["line"] == 11 and payload["comments"][0]["side"] == "RIGHT"
    assert "Password logged" in payload["comments"][0]["body"]


def test_post_review_falls_back_to_summary_when_lines_rejected():
    gh = FakeGitHub(reject_inline=True)
    result = Reviewer(
        FakeLLM(
            findings_reply(
                [
                    {
                        "line": 11,
                        "severity": "major",
                        "category": "security",
                        "title": "Password logged",
                        "message": "Never log passwords.",
                    }
                ]
            )
        )
    ).review(PR_DIFF)
    gh.client().post_review(parse_pr_ref("o/r#7"), "abc123", result)
    assert len(gh.posted) == 2
    assert "comments" not in gh.posted[1] and "Password logged" in gh.posted[1]["body"]


# ------------------------------------------------------------------ API server


@pytest.fixture
def app_parts(settings, indexed):
    settings = settings.with_overrides(github_webhook_secret="hooksecret")
    services = build_services(settings, use_llm=False)
    llm = FakeLLM(
        lambda system, user: (
            findings_reply(
                [
                    {
                        "line": 11,
                        "severity": "critical",
                        "category": "security",
                        "title": "Password logged",
                        "message": "Never log passwords.",
                    }
                ]
            )
            if "Diff" in user
            else "Answer citing [1]."
        )
    )
    services.llm = llm
    services.assistant.llm = llm
    services.reviewer = Reviewer(llm, services.assistant)
    gh = FakeGitHub()
    app = create_app(settings, services, gh.client())
    return TestClient(app), gh, settings


def test_health_stats_and_ui(app_parts):
    client, _, _ = app_parts
    assert client.get("/health").json()["status"] == "ok"
    stats = client.get("/stats").json()
    assert stats["index"]["files"] == 4 and stats["github_enabled"]
    assert "RepoScout" in client.get("/").text
    page = client.get("/").text
    assert "{{" not in page
    # The built UI links its content-hashed stylesheet; without a build the page explains how to build.
    styles = [part.split('"')[0] for part in page.split('rel="stylesheet" crossorigin href="')[1:]]
    assert styles if static_files.built() else static_files.BUILD_COMMAND in page
    for url in styles:
        css = client.get(url)
        assert css.headers["content-type"].startswith("text/css") and "--blue" in css.text
    assert "GET /health" in client.get("/stats").json()["requests"]


def test_ask_search_review_endpoints(app_parts):
    client, _, _ = app_parts
    answer = client.post("/ask", json={"question": "How are tokens verified?"}).json()
    assert answer["cited"] == [1] and answer["sources"][0]["path"] == "auth/session.py"

    hits = client.post("/search", json={"query": "calculate total"}).json()["results"]
    assert hits[0]["path"] == "billing/invoice.py"

    review = client.post("/review", json={"diff": PR_DIFF, "min_severity": "major"}).json()
    assert review["counts"]["critical"] == 1
    assert all(f["severity"] in ("major", "critical") for f in review["findings"])
    assert "RepoScout review" in review["markdown"]

    assert client.post("/ask", json={"question": "x"}).status_code == 422  # too short


def test_webhook_rejects_bad_signature(app_parts):
    client, _, _ = app_parts
    resp = client.post(
        "/webhooks/github",
        content=b"{}",
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert resp.status_code == 401


def webhook(client, secret, event, payload):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": sign(secret, body),
            "Content-Type": "application/json",
        },
    )


PR_EVENT = {
    "action": "opened",
    "pull_request": {"number": 7, "title": "Log user", "body": "", "draft": False, "head": {"sha": "abc123"}},
    "repository": {"name": "shop", "owner": {"login": "octo"}},
}


def test_webhook_reviews_pull_request_once(app_parts):
    client, gh, settings = app_parts
    secret = settings.github_webhook_secret
    assert webhook(client, secret, "ping", {}).json() == {"status": "pong"}

    resp = webhook(client, secret, "pull_request", PR_EVENT)
    assert resp.status_code == 202
    # TestClient runs background tasks before returning, so the review is already posted.
    (payload,) = gh.posted
    assert payload["commit_id"] == "abc123"
    assert payload["comments"][0]["line"] == 11
    reviews = client.get("/reviews").json()["reviews"]
    assert reviews[0]["status"] == "posted" and reviews[0]["pull_request"] == "octo/shop#7"

    again = webhook(client, secret, "pull_request", PR_EVENT)
    assert again.json()["status"] == "duplicate" and len(gh.posted) == 1


def test_webhook_ignores_drafts_and_other_events(app_parts):
    client, gh, settings = app_parts
    secret = settings.github_webhook_secret
    draft = json.loads(json.dumps(PR_EVENT))
    draft["pull_request"]["draft"] = True
    assert webhook(client, secret, "pull_request", draft).json()["status"] == "ignored"
    closed = {**PR_EVENT, "action": "closed"}
    assert webhook(client, secret, "pull_request", closed).json()["status"] == "ignored"
    assert webhook(client, secret, "issues", {}).json()["status"] == "ignored"
    assert gh.posted == []


def test_whoami_reports_the_posting_account():
    import httpx

    from reposcout.github import GitHubClient

    def handler(request):
        if request.headers["authorization"] == "Bearer good":
            return httpx.Response(200, json={"login": "octocat"})
        return httpx.Response(403, json={"message": "Resource not accessible by integration"})

    transport = httpx.MockTransport(handler)
    assert GitHubClient("good", client=httpx.Client(transport=transport)).whoami() == "octocat"
    assert GitHubClient("actions", client=httpx.Client(transport=transport)).whoami() is None
