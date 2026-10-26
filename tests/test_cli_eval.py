import json
from pathlib import Path

import pytest

from reposcout.cli import main
from reposcout.evaluation import eval_retrieval, eval_review, load_cases
from reposcout.review import Reviewer
from reposcout.review.diff import parse_unified_diff

ROOT = Path(__file__).resolve().parents[1]
HASH = ["--embed-provider", "hash"]


def test_cli_index_search_ask_status(sample_repo, capsys):
    assert main(["index", "--repo", str(sample_repo), *HASH]) == 0
    assert "4 indexed" in capsys.readouterr().out

    assert main(["search", "session token signature", "--repo", str(sample_repo), *HASH]) == 0
    assert "auth/session.py" in capsys.readouterr().out

    assert main(["ask", "where is tax added?", "--no-llm", "--repo", str(sample_repo), *HASH]) == 0
    assert "billing/invoice.py" in capsys.readouterr().out

    assert main(["status", "--repo", str(sample_repo), *HASH]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == 4


def test_cli_review_diff_file_and_fail_on(sample_repo, tmp_path, capsys):
    diff = tmp_path / "change.diff"
    diff.write_text("--- a/app.py\n+++ b/app.py\n@@ -1,0 +1,1 @@\n+subprocess.run(cmd, shell=True)\n")
    args = ["review", "--diff", str(diff), "--no-llm", "--repo", str(sample_repo), *HASH]
    assert main(args) == 0
    assert "subprocess with shell=True" in capsys.readouterr().out
    assert main([*args, "--fail-on", "major"]) == 1
    assert main([*args, "--fail-on", "critical"]) == 0
    capsys.readouterr()
    assert main([*args, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["major"] == 1


def test_cli_reports_friendly_errors(sample_repo, capsys):
    code = main(["review-pr", "nonsense", "--no-llm", "--repo", str(sample_repo), *HASH])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_review_dataset_labels_point_at_added_lines():
    for case in load_cases(ROOT / "evals" / "review_cases.json"):
        files = {f.path: f for f in parse_unified_diff(case["diff"])}
        for exp in case["expected"]:
            assert exp["line"] in files[exp["path"]].added_line_numbers(), case["name"]


def test_rules_only_review_eval_has_no_false_alarms():
    report = eval_review(Reviewer(None), load_cases(ROOT / "evals" / "review_cases.json"))
    assert report["precision"] == 1.0
    assert report["clean_case_false_alarm_rate"] == 0.0
    assert report["recall"] >= 0.7  # the remaining misses are logic bugs only an LLM can catch


def test_retrieval_eval_on_sample_repo(make_assistant):
    cases = [
        {"question": "verify the session token signature", "expected": ["auth/session.py::verify_token"]},
        {"question": "remove a product from the cart", "expected": ["web/cart.js::removeFromCart"]},
        {"question": "something that does not exist at all", "expected": ["nowhere.py"]},
    ]
    report = eval_retrieval(make_assistant(), cases, k=3)
    assert report["cases"] == 3
    assert report["recall@3"] == pytest.approx(2 / 3, abs=0.01)
    assert len(report["misses"]) == 1


def test_env_file_is_loaded_without_overriding_the_shell(tmp_path, monkeypatch):
    import os

    from reposcout.config import Settings, load_env_file

    monkeypatch.setattr(os, "environ", dict(os.environ))  # keep loaded values out of other tests

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "REPOSCOUT_LLM_MODEL='from-file'\n"
        'export REPOSCOUT_TOP_K="5"\n'
        "REPOSCOUT_EMBED_PROVIDER=hash  # inline comment\n"
        "REPOSCOUT_OPENAI_API_KEY=\n"
        "REPOSCOUT_LLM_PROVIDER=openai\n"
    )
    for name in (
        "REPOSCOUT_LLM_MODEL",
        "REPOSCOUT_TOP_K",
        "REPOSCOUT_EMBED_PROVIDER",
        "REPOSCOUT_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REPOSCOUT_LLM_PROVIDER", "ollama")  # already set in the shell

    loaded = load_env_file(env_file)
    settings = Settings()
    assert settings.llm_model == "from-file"
    assert settings.top_k == 5
    assert settings.embed_provider == "hash"
    assert settings.llm_provider == "ollama"  # the shell wins
    assert "REPOSCOUT_OPENAI_API_KEY" not in loaded  # empty values are skipped
    assert load_env_file(tmp_path / "missing.env") == []


def test_cli_reads_env_file_from_current_folder(sample_repo, monkeypatch, capsys):
    import os

    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("REPOSCOUT_EMBED_PROVIDER", raising=False)
    monkeypatch.chdir(sample_repo)
    (sample_repo / ".env").write_text("REPOSCOUT_EMBED_PROVIDER=hash\n")
    assert main(["index"]) == 0
    assert "hash-1024" in capsys.readouterr().err


class FakePRClient:
    """Stands in for GitHubClient in `review-pr`; records what would be posted."""

    instances: list = []

    def __init__(self, token, api_url):
        self.posted = []
        FakePRClient.instances.append(self)

    def get_pull_request(self, ref):
        return {"title": "Run commands", "body": "", "head": {"sha": "abc123", "repo": {"full_name": "o/r"}}}

    def get_pull_request_diff(self, ref):
        return "--- a/app.py\n+++ b/app.py\n@@ -1,0 +1,1 @@\n+subprocess.run(cmd, shell=True)\n"

    def source_loader(self, pr):
        return None

    def whoami(self):
        return "octocat"

    def post_review(self, ref, sha, result):
        self.posted.append((str(ref), sha))
        return {"html_url": "https://github.com/o/r/pull/1#review"}


@pytest.fixture
def fake_github(monkeypatch):
    import reposcout.github as github

    FakePRClient.instances = []
    monkeypatch.setattr(github, "GitHubClient", FakePRClient)
    return FakePRClient


class Stdin:
    def __init__(self, tty: bool):
        self.tty = tty

    def isatty(self):
        return self.tty


def review_pr(sample_repo, *extra):
    return main(["review-pr", "o/r#1", "--no-llm", "--repo", str(sample_repo), *HASH, "--post", *extra])


def test_posting_needs_confirmation_without_a_terminal(sample_repo, fake_github, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", Stdin(tty=False))
    assert review_pr(sample_repo) == 2
    assert fake_github.instances[0].posted == []
    assert "--yes" in capsys.readouterr().err
    assert review_pr(sample_repo, "--yes") == 0
    assert fake_github.instances[1].posted == [("o/r#1", "abc123")]


def test_posting_asks_and_respects_the_answer(sample_repo, fake_github, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", Stdin(tty=True))
    asked = []
    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or next(answers))
    assert review_pr(sample_repo) == 0
    assert fake_github.instances[0].posted == [] and "Not posted." in capsys.readouterr().err
    assert review_pr(sample_repo) == 0
    assert fake_github.instances[1].posted == [("o/r#1", "abc123")]
    assert "o/r#1 as @octocat (1 review, 1 inline comment)" in asked[0]
