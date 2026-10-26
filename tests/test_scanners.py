import os
import shutil
from pathlib import Path

import pytest

from reposcout.review import Reviewer
from reposcout.review.diff import parse_unified_diff
from reposcout.review.scanners import (
    BanditScanner,
    GitleaksScanner,
    SemgrepScanner,
    make_scanners,
    run_scanners,
    working_tree_loader,
)

PY_DIFF = """diff --git a/app/run.py b/app/run.py
--- a/app/run.py
+++ b/app/run.py
@@ -1,4 +1,5 @@
 import pickle
 import subprocess
 def run(cmd, blob):
+    subprocess.call(cmd, shell=True)
     return pickle.loads(blob)
"""
PY_SOURCE = (
    "import pickle\nimport subprocess\ndef run(cmd, blob):\n"
    "    subprocess.call(cmd, shell=True)\n    return pickle.loads(blob)\n"
)

GITLEAKS = os.environ.get("REPOSCOUT_GITLEAKS_BIN") or shutil.which("gitleaks")
needs_gitleaks = pytest.mark.skipif(not GITLEAKS, reason="gitleaks is not installed")
needs_bandit = pytest.mark.skipif(not BanditScanner.available(), reason="bandit is not installed")


def only(diff: str):
    (file,) = parse_unified_diff(diff)
    return file


@needs_bandit
def test_bandit_reports_only_added_lines():
    findings = BanditScanner().scan(only(PY_DIFF), PY_SOURCE)
    assert [(f.line, f.source, f.severity.value) for f in findings] == [(4, "rule:bandit-B602", "critical")]
    # pickle.loads on line 5 is a real issue too, but this change didn't add it.


@needs_bandit
def test_bandit_skips_tests_and_other_languages():
    scanner = BanditScanner()
    assert scanner.wants_source("app/run.py")
    assert not scanner.wants_source("tests/test_run.py") and not scanner.wants_source("web/app.js")
    assert scanner.scan(only(PY_DIFF), None) == []


@needs_gitleaks
def test_gitleaks_maps_leaks_to_diff_lines():
    diff = """diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1,1 +1,3 @@
 DEBUG = False
+REGION = "us-east-1"
+AWS_KEY = "AKIAQWERTYUIOPASDFGH"
"""
    findings = GitleaksScanner(GITLEAKS).scan(only(diff), None)
    assert [(f.line, f.severity.value) for f in findings] == [(3, "critical")]
    assert findings[0].source.startswith("rule:gitleaks-") and "AKIA" not in findings[0].message


def test_missing_tools_are_skipped(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("REPOSCOUT_GITLEAKS_BIN", raising=False)
    monkeypatch.delenv("REPOSCOUT_SEMGREP_BIN", raising=False)
    names = [s.name for s in make_scanners("gitleaks,semgrep,unknown")]
    assert names == []
    assert make_scanners("none") == []


def test_source_is_loaded_only_when_a_scanner_needs_it():
    loaded = []

    def loader(path):
        loaded.append(path)
        return PY_SOURCE

    class LineOnly:
        name = "line-only"

        def wants_source(self, path):
            return False

        def scan(self, file, source):
            assert source is None
            return []

    run_scanners([LineOnly()], only(PY_DIFF), loader)
    assert loaded == []


def test_a_crashing_scanner_never_fails_the_review():
    class Broken:
        name = "broken"

        def wants_source(self, path):
            return True

        def scan(self, file, source):
            raise RuntimeError("boom")

    result = Reviewer(None, scanners=[Broken()]).review(PY_DIFF, load_source=lambda p: PY_SOURCE)
    assert result.files_reviewed == ["app/run.py"]


def test_working_tree_loader_stays_inside_the_checkout(tmp_path: Path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "a.py").write_text("x = 1\n")
    (tmp_path / "secret.txt").write_text("hunter2")
    (tmp_path / "repo" / "link.py").symlink_to(tmp_path / "secret.txt")
    load = working_tree_loader(tmp_path / "repo")
    assert load("a.py") == "x = 1\n"
    assert load("link.py") is None
    assert load("../secret.txt") is None
    assert load("missing.py") is None


@pytest.mark.skipif(not shutil.which("semgrep"), reason="semgrep is not installed")
def test_semgrep_scanner_runs_with_a_local_rule(tmp_path: Path):
    rule = tmp_path / "rule.yml"
    rule.write_text(
        "rules:\n- id: no-shell-true\n  languages: [python]\n  severity: ERROR\n"
        "  message: shell=True runs a shell\n  pattern: subprocess.call(..., shell=True)\n"
    )
    findings = SemgrepScanner(config=str(rule)).scan(only(PY_DIFF), PY_SOURCE)
    assert [(f.line, f.source) for f in findings] == [(4, "rule:semgrep-no-shell-true")]
