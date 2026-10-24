import json
import shutil
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from reposcout.embeddings import HashingEmbedder
from reposcout.web.app import create_public_app
from reposcout.web.config import PublicSettings
from reposcout.web.github_public import GitHubAPIError
from reposcout.web.links import LinkError, RepoId, parse_github_link, pr_line_url
from reposcout.web.workspace import RepoLimitError, Workspace

# ------------------------------------------------------------------ links


@pytest.mark.parametrize(
    "text, owner, name, pull",
    [
        ("https://github.com/pallets/flask", "pallets", "flask", None),
        ("github.com/pallets/flask.git", "pallets", "flask", None),
        ("https://github.com/pallets/flask/", "pallets", "flask", None),
        ("https://github.com/pallets/flask/tree/main/src", "pallets", "flask", None),
        ("https://github.com/pallets/flask/pull/5432", "pallets", "flask", 5432),
        ("https://github.com/pallets/flask/pull/5432/files", "pallets", "flask", 5432),
        ("pallets/flask", "pallets", "flask", None),
        ("pallets/flask#12", "pallets", "flask", 12),
        ("  http://www.github.com/osm-search/Nominatim  ", "osm-search", "Nominatim", None),
    ],
)
def test_parse_github_link(text, owner, name, pull):
    link = parse_github_link(text)
    assert (link.repo.owner, link.repo.name, link.pull) == (owner, name, pull)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "flask",
        "https://gitlab.com/a/b",
        "https://github.com/a",
        "a/..",
        "-bad/name",
        "file:///etc/passwd",
    ],
)
def test_parse_github_link_rejects(text):
    with pytest.raises(LinkError):
        parse_github_link(text)


def test_repo_key_and_pr_line_url():
    repo = RepoId("Pallets", "Flask")
    assert repo.key == "pallets__flask"
    url = pr_line_url(repo, 7, "src/app.py", 12)
    assert url.startswith("https://github.com/Pallets/Flask/pull/7/files#diff-")
    assert url.endswith("R12") and len(url.split("#diff-")[1]) == 64 + 3


# ------------------------------------------------------------------ workspace


@pytest.fixture
def public_settings(tmp_path) -> PublicSettings:
    return PublicSettings(
        data_dir=tmp_path / "data",
        max_repos=5,
        max_files=100,
        max_chunks=1000,
        github_token="",
        trust_proxy=False,
        index_per_hour=50,
        llm_per_10min=50,
    )


def copy_cloner(source: Path, commit: str = "c0ffee1234"):
    calls = []

    def clone(repo: RepoId, dest: Path, timeout: int) -> str:
        calls.append(repo.full_name)
        shutil.copytree(source, dest, symlinks=True)
        return commit

    clone.calls = calls
    return clone


def make_workspace(public_settings, sample_repo, **kwargs):
    return Workspace(
        public_settings,
        HashingEmbedder(),
        kwargs.pop("github", None),
        cloner=kwargs.pop("cloner", copy_cloner(sample_repo)),
    )


def age_index(ws, repo, seconds=3700):
    """Pretend the index was built a while ago, so a refresh is allowed."""
    ws._update(repo.key, indexed_at=time.time() - seconds)


def test_submit_index_and_reuse(public_settings, sample_repo):
    ws = make_workspace(public_settings, sample_repo)
    repo = RepoId("Octo", "Shop")
    record = ws.submit(repo)
    assert record["status"] == "queued" and record["queue_position"] == 1
    ws.drain()
    record = ws.get(repo)
    assert record["status"] == "ready" and record["ready"]
    assert record["commit_sha"] == "c0ffee1234" and record["chunks"] > 4
    assert record["files_done"] == record["files_total"] == 4
    store = ws.store(repo)
    assert store.find_symbol("verify_token")[0].path == "auth/session.py"
    # Fresh repos aren't cloned again; a refresh is.
    ws.submit(repo)
    ws.drain()
    assert len(ws.cloner.calls) == 1
    # A refresh right after indexing is ignored (anyone could otherwise keep the queue busy)...
    ws.submit(repo, force=True)
    ws.drain()
    assert len(ws.cloner.calls) == 1
    # ...but works once the index is an hour old.
    age_index(ws, repo)
    ws.submit(repo, force=True)
    ws.drain()
    assert len(ws.cloner.calls) == 2 and ws.get(repo)["status"] == "ready"
    assert [r["full_name"] for r in ws.list()] == ["Octo/Shop"]


def test_failures_are_reported_and_refresh_failure_keeps_old_index(public_settings, sample_repo):
    def broken(repo, dest, timeout):
        raise RepoLimitError("Repository not found. Only public GitHub repositories are supported.")

    ws = make_workspace(public_settings, sample_repo, cloner=broken)
    repo = RepoId("octo", "missing")
    ws.submit(repo)
    ws.drain()
    record = ws.get(repo)
    assert record["status"] == "failed" and "not found" in record["error"]
    assert not record["ready"]

    ws = make_workspace(public_settings, sample_repo)
    good = RepoId("octo", "shop")
    ws.submit(good)
    ws.drain()
    ws.cloner = broken
    age_index(ws, good)
    ws.submit(good, force=True)
    ws.drain()
    record = ws.get(good)
    assert record["status"] == "ready" and record["ready"] and "not found" in record["error"]
    assert ws.store(good).stats()["chunks"] > 0


def test_size_limits(public_settings, sample_repo):
    small = PublicSettings(data_dir=public_settings.data_dir, max_files=2)
    ws = Workspace(small, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    repo = RepoId("octo", "shop")
    ws.submit(repo)
    ws.drain()
    assert ws.get(repo)["status"] == "failed"
    assert "indexes up to 2" in ws.get(repo)["error"]
    # Nothing is left behind from the failed attempt.
    assert not any(public_settings.data_dir.joinpath("repos").iterdir())


def test_metadata_checks(public_settings, sample_repo):
    class FakeGitHub:
        def __init__(self, meta=None, error=None):
            self.meta, self.error = meta, error

        def repo(self, repo):
            if self.error:
                raise self.error
            return self.meta

    ws = make_workspace(
        public_settings,
        sample_repo,
        github=FakeGitHub({"private": False, "size": 10**6, "full_name": "Octo/Shop"}),
    )
    ws.submit(RepoId("octo", "big"))
    ws.drain()
    assert "MB" in ws.get(RepoId("octo", "big"))["error"]

    meta = {
        "private": False,
        "size": 12,
        "full_name": "Octo/Shop",
        "default_branch": "main",
        "description": "A shop",
        "language": "Python",
        "stargazers_count": 42,
    }
    ws.github = FakeGitHub(meta)
    ws.submit(RepoId("octo", "shop"))
    ws.drain()
    record = ws.get(RepoId("octo", "shop"))
    assert record["status"] == "ready" and record["stars"] == 42 and record["branch"] == "main"

    # If GitHub's API is unavailable (rate limited), indexing still works.
    ws.github = FakeGitHub(error=GitHubAPIError("limit", 429))
    ws.submit(RepoId("octo", "other"))
    ws.drain()
    assert ws.get(RepoId("octo", "other"))["status"] == "ready"


def test_eviction_and_restart(public_settings, sample_repo):
    one = PublicSettings(data_dir=public_settings.data_dir, max_repos=1)
    ws = Workspace(one, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    ws.submit(RepoId("a", "first"))
    ws.drain()
    ws.submit(RepoId("a", "second"))
    ws.drain()
    assert ws.get(RepoId("a", "first")) is None
    assert ws.get(RepoId("a", "second"))["ready"]

    # A job that was queued when the server stopped is picked up after a restart.
    ws.submit(RepoId("a", "third"))
    restarted = Workspace(one, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    restarted.drain()
    assert restarted.get(RepoId("a", "third"))["status"] == "ready"


def test_symlinks_in_a_repo_are_never_indexed(public_settings, sample_repo, tmp_path):
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("TOP_SECRET=1\n")
    (sample_repo / "leak.py").symlink_to(secret)
    ws = make_workspace(public_settings, sample_repo)
    repo = RepoId("octo", "shop")
    ws.submit(repo)
    ws.drain()
    assert "leak.py" not in ws.store(repo).file_hashes()


# ------------------------------------------------------------------ API


PR_DIFF = """diff --git a/billing/invoice.py b/billing/invoice.py
--- a/billing/invoice.py
+++ b/billing/invoice.py
@@ -6,4 +6,5 @@ TAX_RATE = 0.18
 def calculate_total(items, discount=0.0):
     \"\"\"Sum item prices, apply a discount and add tax.\"\"\"
     subtotal = sum(item.price * item.quantity for item in items)
-    return round((subtotal - discount) * (1 + TAX_RATE), 2)
+    total = (subtotal - discount) * (1 + TAX_RATE)
+    return total / len(items)
"""


class FakeGitHub:
    def __init__(self, files=1, lines=2):
        self.files, self.lines = files, lines

    def repo(self, repo):
        raise GitHubAPIError("offline", 503)

    def pull(self, repo, number):
        if number == 404:
            raise GitHubAPIError("Not found", 404)
        return {
            "title": "Average invoice totals",
            "body": "",
            "html_url": f"https://github.com/octo/shop/pull/{number}",
            "user": {"login": "dev"},
            "state": "open",
            "merged_at": None,
            "additions": self.lines,
            "deletions": 1,
            "changed_files": self.files,
            "base": {"repo": {"full_name": "Octo/Shop"}},
        }

    def pull_diff(self, repo, number):
        return PR_DIFF


class FakeGroq:
    """Stands in for the provider: /models and /chat/completions."""

    def __init__(self):
        self.chat_requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer gsk_good":
            return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "openai/gpt-oss-20b", "active": True},
                        {"id": "whisper-large-v3", "active": True},
                        {"id": "qwen/qwen3.8-27b", "active": True},
                        {"id": "meta-llama/llama-prompt-guard-2-86m"},
                    ]
                },
            )
        body = json.loads(request.content)
        self.chat_requests.append(body)
        user = body["messages"][1]["content"]
        if body.get("response_format"):
            content = json.dumps(
                {
                    "summary": "Averages the total.",
                    "findings": [
                        {
                            "line": 10,
                            "severity": "major",
                            "category": "bug",
                            "title": "Division by zero",
                            "message": "len(items) is 0 for an empty invoice.",
                            "suggestion": "return total",
                        }
                    ],
                }
            )
        else:
            content = (
                "Tokens are verified with HMAC in verify_token [1]."
                if "token" in user.lower()
                else "See [1]."
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )


@pytest.fixture
def api(public_settings, sample_repo):
    groq = FakeGroq()
    github = FakeGitHub()
    ws = make_workspace(public_settings, sample_repo)
    app = create_public_app(
        public_settings,
        ws,
        HashingEmbedder(),
        github,
        http=httpx.Client(transport=httpx.MockTransport(groq.handler)),
    )
    return TestClient(app), ws, groq, github


KEY = {"X-LLM-Key": "gsk_good"}


def test_full_flow(api):
    client, ws, groq, _ = api
    assert "RepoScout" in client.get("/").text
    page = client.get("/").text
    assert "/ui.css?v=" in page and "{{asset_version}}" not in page
    css = client.get("/ui.css")
    assert css.headers["content-type"].startswith("text/css") and "--accent" in css.text
    assert client.get("/api/config").json()["provider"] == "Groq"

    resp = client.post("/api/repos", json={"url": "https://github.com/Octo/Shop"})
    assert resp.status_code == 202 and resp.json()["repo"]["status"] == "queued"
    assert (
        client.post("/api/repos/octo/shop/ask", json={"question": "hi there"}, headers=KEY).status_code == 409
    )
    ws.drain()
    assert client.get("/api/repos/octo/shop").json()["repo"]["status"] == "ready"
    assert client.get("/api/repos").json()["repos"][0]["full_name"] == "Octo/Shop"

    no_key = client.post("/api/repos/octo/shop/ask", json={"question": "How are tokens verified?"})
    assert no_key.status_code == 401 and no_key.json()["code"] == "no_key"

    answer = client.post(
        "/api/repos/octo/shop/ask",
        json={
            "question": "How are tokens verified?",
            "history": [{"question": "What is this?", "answer": "A shop."}],
        },
        headers={**KEY, "X-LLM-Model": "qwen/qwen3.8-27b"},
    ).json()
    assert answer["cited"] == [1] and answer["model"] == "qwen/qwen3.8-27b"
    assert answer["sources"][0]["url"].startswith(
        "https://github.com/Octo/Shop/blob/c0ffee1234/auth/session.py#L"
    )
    assert "Repository: Octo/Shop" in groq.chat_requests[-1]["messages"][1]["content"]

    hits = client.post("/api/repos/octo/shop/search", json={"query": "calculate total"}).json()["results"]
    assert hits[0]["path"] == "billing/invoice.py" and "/blob/c0ffee1234/" in hits[0]["url"]


def test_models_and_bad_keys(api):
    client, *_ = api
    models = client.post("/api/models", headers=KEY).json()
    assert models["models"] == ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]  # whisper and guard removed
    assert models["default"] == "openai/gpt-oss-20b"
    bad = client.post("/api/models", headers={"X-LLM-Key": "gsk_bad"})
    assert bad.status_code == 401 and bad.json()["code"] == "bad_key"
    weird_model = client.post(
        "/api/review", json={"url": "octo/shop#5"}, headers={**KEY, "X-LLM-Model": "bad model; rm -rf"}
    )
    assert weird_model.status_code == 400


def test_review_pull_request(api):
    client, ws, groq, github = api
    resp = client.post("/api/review", json={"url": "https://github.com/octo/shop/pull/5"}, headers=KEY)
    data = resp.json()
    assert resp.status_code == 200, data
    assert data["pull"]["title"] == "Average invoice totals" and data["pull"]["repo"] == "Octo/Shop"
    finding = data["findings"][0]
    assert finding["title"] == "Division by zero"
    assert finding["url"].startswith("https://github.com/Octo/Shop/pull/5/files#diff-")
    assert data["used_repo_context"] is False and "RepoScout review" in data["markdown"]

    # Once the repo is indexed, reviews use its code as context.
    client.post("/api/repos", json={"url": "octo/shop"})
    ws.drain()
    assert client.post("/api/review", json={"url": "octo/shop#5"}, headers=KEY).json()["used_repo_context"]

    bad_key = client.post("/api/review", json={"url": "octo/shop#5"}, headers={"X-LLM-Key": "gsk_bad"})
    assert bad_key.status_code == 401 and bad_key.json()["code"] == "bad_key"
    assert client.post("/api/review", json={"url": "octo/shop"}, headers=KEY).json()["code"] == "not_a_pull"
    assert client.post("/api/review", json={"url": "octo/shop#404"}, headers=KEY).status_code == 404
    github.files = 500
    too_big = client.post("/api/review", json={"url": "octo/shop#6"}, headers=KEY)
    assert too_big.status_code == 413 and too_big.json()["code"] == "pr_too_big"


def test_rate_limits_and_bad_links(public_settings, sample_repo):
    strict = PublicSettings(data_dir=public_settings.data_dir, index_per_hour=1, llm_per_10min=1)
    ws = Workspace(strict, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    app = create_public_app(
        strict,
        ws,
        HashingEmbedder(),
        FakeGitHub(),
        http=httpx.Client(transport=httpx.MockTransport(FakeGroq().handler)),
    )
    client = TestClient(app)
    assert client.post("/api/repos", json={"url": "nonsense"}).json()["code"] == "bad_link"
    assert client.post("/api/repos", json={"url": "octo/one"}).status_code == 202
    # Asking again about a repo that's already queued doesn't count against the limit.
    assert client.post("/api/repos", json={"url": "octo/one"}).status_code == 202
    limited = client.post("/api/repos", json={"url": "octo/two"})
    assert limited.status_code == 429 and limited.json()["code"] == "too_many_requests"
    assert "Retry-After" in limited.headers


def test_real_cloner_refuses_local_file_urls(tmp_path):
    """git_clone only fetches over the network: file:// clones are blocked, so a crafted
    repository id can never make the server copy its own folders."""
    import subprocess

    from reposcout.errors import RepoScoutError
    from reposcout.web.workspace import git_clone

    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
    (origin / "app.py").write_text("print('hi')\n")
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", "commit", "-qm", "init"], cwd=origin, check=True
    )

    class LocalRepo(RepoId):
        @property
        def clone_url(self):
            return f"file://{origin}"

    with pytest.raises(RepoScoutError):
        git_clone(LocalRepo("o", "r"), tmp_path / "clone", 30)
    assert not (tmp_path / "clone" / "app.py").exists()


def test_only_a_few_indexes_stay_open(public_settings, sample_repo, monkeypatch):
    from reposcout.web import workspace as ws_module

    monkeypatch.setattr(ws_module, "MAX_OPEN_STORES", 2)
    ws = make_workspace(public_settings, sample_repo)
    repos = [RepoId("o", f"r{i}") for i in range(3)]
    for repo in repos:
        ws.submit(repo)
    ws.drain()
    for repo in repos:
        assert ws.store(repo) is not None
    assert len(ws._stores) == 2 and repos[0].key not in ws._stores
    # A closed index is simply reopened when needed.
    assert ws.store(repos[0]).find_symbol("verify_token")


def test_refresh_swaps_index_files_without_disturbing_readers(public_settings, sample_repo):
    ws = make_workspace(public_settings, sample_repo)
    repo = RepoId("octo", "shop")
    ws.submit(repo)
    ws.drain()
    reader = ws.store(repo)  # a request holding the old index
    age_index(ws, repo)
    ws.submit(repo, force=True)
    ws.drain()
    assert reader.find_symbol("verify_token")  # still readable after the swap
    assert ws.store(repo) is not reader
    index_files = sorted(p.name for p in (public_settings.data_dir / "indexes").glob("*.db"))
    assert len(index_files) == 1 and index_files[0].startswith("octo__shop.")
    assert not list((public_settings.data_dir / "repos").iterdir())  # checkouts are cleaned up


# ------------------------------------------------------------------ abuse protection


def _local_origin(tmp_path, files: dict[str, str], symlinks: dict[str, str] | None = None) -> Path:
    import subprocess

    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
    for rel, text in files.items():
        (origin / rel).parent.mkdir(parents=True, exist_ok=True)
        (origin / rel).write_text(text)
    for rel, target in (symlinks or {}).items():
        (origin / rel).symlink_to(target)
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", "commit", "-qm", "init"], cwd=origin, check=True
    )
    return origin


def _local_clone(monkeypatch, origin):
    """git_clone pointed at a local folder (file:// is normally refused, see above)."""
    from reposcout.web import workspace as ws_module

    monkeypatch.setattr(
        ws_module, "GIT_SAFE", ["-c", "core.symlinks=false", "-c", "protocol.file.allow=always"]
    )

    class LocalRepo(RepoId):
        @property
        def clone_url(self):
            return f"file://{origin}"

    return ws_module.git_clone, LocalRepo("o", "r")


def test_cloner_checks_out_only_small_regular_files(tmp_path, monkeypatch):
    origin = _local_origin(
        tmp_path,
        {"app.py": "print('hi')\n", "pkg/util.py": "x = 1\n", "data/huge.json": "[" + "1," * 5000 + "1]"},
        symlinks={"leak": "/etc/passwd"},
    )
    git_clone, repo = _local_clone(monkeypatch, origin)
    dest = tmp_path / "clone"
    commit = git_clone(repo, dest, 60, max_bytes=1_000_000, max_file_bytes=2000)
    assert len(commit) == 40
    assert (dest / "app.py").read_text() == "print('hi')\n" and (dest / "pkg" / "util.py").exists()
    assert not (dest / "data" / "huge.json").exists()  # over the per-file limit
    assert not (dest / "leak").exists() and not (dest / "leak").is_symlink()


def test_cloner_refuses_repositories_over_the_limits(tmp_path, monkeypatch):
    origin = _local_origin(tmp_path, {f"f{i}.py": "y = 2\n" * 200 for i in range(6)})
    git_clone, repo = _local_clone(monkeypatch, origin)
    with pytest.raises(RepoLimitError, match="larger than"):
        git_clone(repo, tmp_path / "a", 60, max_bytes=3000, max_file_bytes=10_000)
    with pytest.raises(RepoLimitError, match="files"):
        git_clone(repo, tmp_path / "b", 60, max_bytes=1_000_000, max_files=3)


def test_queue_caps(public_settings, sample_repo):
    settings = PublicSettings(data_dir=public_settings.data_dir, max_queued=2)
    ws = Workspace(settings, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    from reposcout.web.workspace import QueueFullError

    ws.submit(RepoId("a", "one"), requested_by="1.1.1.1")
    with pytest.raises(QueueFullError, match="already have"):
        ws.submit(RepoId("a", "two"), requested_by="1.1.1.1")  # one job per visitor
    ws.submit(RepoId("a", "two"), requested_by="2.2.2.2")
    with pytest.raises(QueueFullError, match="busy"):
        ws.submit(RepoId("a", "three"), requested_by="3.3.3.3")  # server-wide cap
    # Re-submitting something already queued is always fine.
    assert ws.submit(RepoId("a", "one"), requested_by="3.3.3.3")["status"] == "queued"
    assert "requested_by" not in ws.get(RepoId("a", "one"))
    ws.drain()
    ws.submit(RepoId("a", "three"), requested_by="3.3.3.3")


def test_old_failed_rows_are_pruned(public_settings, sample_repo):
    def broken(repo, dest, timeout):
        raise RepoLimitError("nope")

    ws = make_workspace(public_settings, sample_repo, cloner=broken)
    ws.submit(RepoId("a", "bad"))
    ws.drain()
    assert ws.get(RepoId("a", "bad"))["status"] == "failed"
    ws._update("a__bad", status="failed")
    with ws._db:
        ws._db.execute("UPDATE repos SET updated_at = ? WHERE key = 'a__bad'", (time.time() - 90_000,))
    ws.cloner = copy_cloner(sample_repo)
    ws.submit(RepoId("a", "good"))
    assert ws.get(RepoId("a", "bad")) is None


def test_index_time_budget(public_settings, sample_repo):
    settings = PublicSettings(data_dir=public_settings.data_dir, index_timeout_minutes=0)
    ws = Workspace(settings, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    ws.submit(RepoId("a", "slow"))
    ws.drain()
    record = ws.get(RepoId("a", "slow"))
    assert record["status"] == "failed" and "longer than" in record["error"]


def test_request_body_size_limit(api):
    client, *_ = api
    resp = client.post("/api/repos", content=b"x" * 70_000, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413 and resp.json()["code"] == "too_large"

    def chunks():
        for _ in range(20):
            yield b"x" * 8192

    streamed = client.post("/api/repos", content=chunks(), headers={"Content-Type": "application/json"})
    assert streamed.status_code == 413


def test_concurrency_limit():
    from reposcout.web.limits import Busy, ConcurrencyLimit

    limit = ConcurrencyLimit(total=2, per_key=1)
    with limit.slot("a"):
        with pytest.raises(Busy, match="visitor"), limit.slot("a"):
            pass
        with limit.slot("b"), pytest.raises(Busy, match="server"), limit.slot("c"):
            pass
    with limit.slot("a"), limit.slot("c"):  # slots are given back
        pass


def test_busy_server_turns_requests_away(public_settings, sample_repo):
    groq = FakeGroq()
    seen = {}
    holder = {}

    def provider(request):
        # While this request holds the visitor's slot, a second one from the same visitor is refused.
        if request.url.path.endswith("/chat/completions") and "second" not in seen:
            seen["second"] = holder["client"].post(
                "/api/repos/octo/shop/ask", json={"question": "again?"}, headers=KEY
            )
        return groq.handler(request)

    ws = make_workspace(public_settings, sample_repo)
    app = create_public_app(
        public_settings,
        ws,
        HashingEmbedder(),
        FakeGitHub(),
        http=httpx.Client(transport=httpx.MockTransport(provider)),
    )
    client = holder["client"] = TestClient(app)
    client.post("/api/repos", json={"url": "octo/shop"})
    ws.drain()
    first = client.post(
        "/api/repos/octo/shop/ask", json={"question": "How are tokens verified?"}, headers=KEY
    )
    assert first.status_code == 200
    assert seen["second"].status_code == 429 and seen["second"].json()["code"] == "one_at_a_time"


def test_search_is_rate_limited(public_settings, sample_repo):
    strict = PublicSettings(data_dir=public_settings.data_dir, search_per_10min=2)
    ws = Workspace(strict, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    client = TestClient(create_public_app(strict, ws, HashingEmbedder(), FakeGitHub()))
    client.post("/api/repos", json={"url": "octo/shop"})
    ws.drain()
    for _ in range(2):
        assert client.post("/api/repos/octo/shop/search", json={"query": "total"}).status_code == 200
    assert client.post("/api/repos/octo/shop/search", json={"query": "total"}).status_code == 429


def test_client_ip_behind_proxy(public_settings, sample_repo):
    from reposcout.web.app import visitor_key

    assert visitor_key("2001:db8:1:2:3:4:5:6") == visitor_key("2001:db8:1:2:ffff::1") == "2001:db8:1:2::/64"
    assert visitor_key("::ffff:1.2.3.4") == "1.2.3.4" and visitor_key("1.2.3.4") == "1.2.3.4"

    strict = PublicSettings(data_dir=public_settings.data_dir, index_per_hour=1, trust_proxy=True)
    ws = Workspace(strict, HashingEmbedder(), None, cloner=copy_cloner(sample_repo))
    client = TestClient(create_public_app(strict, ws, HashingEmbedder(), FakeGitHub()))
    proxy = {"X-Real-IP": "9.9.9.9"}
    assert client.post("/api/repos", json={"url": "octo/one"}, headers=proxy).status_code == 202
    ws.drain()
    # A forged X-Forwarded-For doesn't give the same visitor a fresh limit.
    forged = {**proxy, "X-Forwarded-For": "5.5.5.5"}
    assert client.post("/api/repos", json={"url": "octo/two"}, headers=forged).status_code == 429
    # A different real visitor isn't affected.
    other = {"X-Real-IP": "8.8.8.8"}
    assert client.post("/api/repos", json={"url": "octo/two"}, headers=other).status_code == 202


def test_refresh_soon_after_indexing_is_free(api):
    client, ws, *_ = api
    client.post("/api/repos", json={"url": "octo/shop"})
    ws.drain()
    for _ in range(10):  # doesn't count against the per-hour limit and doesn't requeue
        resp = client.post("/api/repos", json={"url": "octo/shop", "refresh": True})
        assert resp.status_code == 202 and resp.json()["repo"]["status"] == "ready"


def test_huge_pr_diffs_are_refused():
    from reposcout.web.github_public import PublicGitHub

    def handler(request):
        return httpx.Response(200, content=b"+x\n" * 400_000)

    gh = PublicGitHub(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(GitHubAPIError) as info:
        gh.pull_diff(RepoId("o", "r"), 1, max_bytes=100_000)
    assert info.value.status == 413
    assert gh.pull_diff(RepoId("o", "r"), 1, max_bytes=2_000_000).startswith("+x")
