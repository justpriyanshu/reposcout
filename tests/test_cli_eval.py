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
