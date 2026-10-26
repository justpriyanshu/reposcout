"""Established static analysers, run next to the built-in regex rules.

* **Bandit** (Python security linter, in-process) reads the whole new version of each
  changed Python file, so it understands the code rather than single lines.
* **gitleaks** (secret scanner, a Go binary) reads only the added lines; its ~200 rules
  know the exact shape of each provider's tokens.
* **Semgrep** (multi-language rules, a separate CLI) is slower to start, so it is opt-in
  here and runs on every pull request in CI instead (see .github/workflows/ci.yml).

Like the regex rules, scanners only report problems on lines the change adds, and their
findings are marked ``rule:<tool>-<id>`` so they win ties against the LLM's.

``REPOSCOUT_SCANNERS`` picks them (default ``bandit,gitleaks``); a scanner that isn't
installed is skipped quietly.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol

from ..chunking import detect_language
from ..paths import is_test_path
from .diff import FileDiff
from .models import Category, Finding, Severity

log = logging.getLogger(__name__)

# The full text of a changed file after the change, or None when it isn't available.
SourceLoader = Callable[[str], "str | None"]
MAX_SOURCE_BYTES = 400_000
SCAN_TIMEOUT = 60


class Scanner(Protocol):
    name: str

    def wants_source(self, path: str) -> bool:
        """True if scanning ``path`` needs the whole file, not just the diff."""
        ...

    def scan(self, file: FileDiff, source: str | None) -> list[Finding]: ...


def _on_added_lines(file: FileDiff, findings: list[Finding]) -> list[Finding]:
    added = {line for line, _ in file.added_lines()}
    return [f for f in findings if f.line in added]


def _temp_dir() -> str:
    return os.environ.get("REPOSCOUT_SCAN_TMPDIR") or tempfile.gettempdir()


# --------------------------------------------------------------------------- bandit

BANDIT_SEVERITY = {"HIGH": Severity.major, "MEDIUM": Severity.minor, "LOW": Severity.nit}
# Bandit's broad "an import of X exists" and test-only checks are noise in a code review.
BANDIT_SKIP = {"B101", "B403", "B404", "B405", "B406", "B407", "B408", "B409", "B410", "B411", "B413"}


class BanditScanner:
    name = "bandit"

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("bandit") is not None

    def wants_source(self, path: str) -> bool:
        return detect_language(path) == "python" and not is_test_path(path)

    def scan(self, file: FileDiff, source: str | None) -> list[Finding]:
        if source is None or not self.wants_source(file.path):
            return []
        # Bandit's plugin loader logs an ERROR for optional report formats (SARIF) that
        # aren't installed; they're irrelevant here.
        logging.getLogger("stevedore.extension").setLevel(logging.CRITICAL)
        from bandit.core import config as bandit_config
        from bandit.core import manager

        with tempfile.TemporaryDirectory(prefix="reposcout-bandit-", dir=_temp_dir()) as tmp:
            target = Path(tmp) / "source.py"
            target.write_text(source, encoding="utf-8")
            bandit = manager.BanditManager(bandit_config.BanditConfig(), "file", quiet=True)
            bandit.discover_files([str(target)])
            bandit.run_tests()
            issues = bandit.get_issue_list()
        findings = []
        for issue in issues:
            if issue.test_id in BANDIT_SKIP or issue.confidence == "LOW":
                continue
            severity = BANDIT_SEVERITY.get(issue.severity, Severity.nit)
            if issue.severity == "HIGH" and issue.confidence == "HIGH":
                severity = Severity.critical
            findings.append(
                Finding(
                    path=file.path,
                    line=max(1, int(issue.lineno)),
                    severity=severity,
                    category=Category.security,
                    title=f"Bandit {issue.test_id}: {issue.test.replace('_', ' ')}"[:200],
                    message=f"{issue.text} (confidence: {issue.confidence.lower()})"[:2000],
                    source=f"rule:bandit-{issue.test_id}",
                )
            )
        return _on_added_lines(file, findings)


# --------------------------------------------------------------------------- gitleaks


class GitleaksScanner:
    name = "gitleaks"

    def __init__(self, binary: str | None = None):
        self.binary = binary or os.environ.get("REPOSCOUT_GITLEAKS_BIN") or shutil.which("gitleaks")

    def available(self) -> bool:
        return bool(self.binary)

    def wants_source(self, path: str) -> bool:
        return False  # secrets are found in the added lines themselves

    def scan(self, file: FileDiff, source: str | None) -> list[Finding]:
        added = file.added_lines()
        if not added or not self.binary:
            return []
        text = "\n".join(content for _, content in added) + "\n"
        try:
            proc = subprocess.run(
                [
                    self.binary,
                    "stdin",
                    "--no-banner",
                    "--redact",
                    "--report-format",
                    "json",
                    "--report-path",
                    "-",
                ],
                input=text.encode("utf-8", "replace"),
                capture_output=True,
                timeout=SCAN_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("gitleaks failed on %s: %s", file.path, exc)
            return []
        if proc.returncode not in (0, 1):  # 1 means "leaks found"
            log.warning("gitleaks exited with %s: %s", proc.returncode, proc.stderr[-300:])
            return []
        try:
            leaks = json.loads(proc.stdout or b"[]") or []
        except json.JSONDecodeError:
            return []
        findings = []
        for leak in leaks:
            index = int(leak.get("StartLine") or 0) - 1  # line in the text we sent
            if not 0 <= index < len(added):
                continue
            rule = str(leak.get("RuleID") or "secret")
            findings.append(
                Finding(
                    path=file.path,
                    line=added[index][0],
                    severity=Severity.critical,
                    category=Category.security,
                    title=f"Secret committed ({rule})"[:200],
                    message=(
                        f"{leak.get('Description') or 'This looks like a credential.'} Remove it from the "
                        "change, rotate it, and load it from a secret store or environment variable."
                    )[:2000],
                    source=f"rule:gitleaks-{rule}",
                )
            )
        return findings


# --------------------------------------------------------------------------- semgrep

SEMGREP_SEVERITY = {"ERROR": Severity.major, "WARNING": Severity.minor, "INFO": Severity.nit}
SEMGREP_LANGUAGES = {"python", "javascript", "typescript", "go", "java", "ruby", "php", "kotlin", "scala", "csharp", "c", "cpp", "rust", "swift"}  # fmt: skip


class SemgrepScanner:
    name = "semgrep"

    def __init__(self, binary: str | None = None, config: str | None = None):
        self.binary = binary or os.environ.get("REPOSCOUT_SEMGREP_BIN") or shutil.which("semgrep")
        self.config = config or os.environ.get("REPOSCOUT_SEMGREP_CONFIG", "p/default")

    def available(self) -> bool:
        return bool(self.binary)

    def wants_source(self, path: str) -> bool:
        return detect_language(path) in SEMGREP_LANGUAGES

    def scan(self, file: FileDiff, source: str | None) -> list[Finding]:
        if source is None or not self.binary or not self.wants_source(file.path):
            return []
        suffix = PurePosixPath(file.path).suffix
        with tempfile.TemporaryDirectory(prefix="reposcout-semgrep-", dir=_temp_dir()) as tmp:
            target = Path(tmp) / f"source{suffix}"
            target.write_text(source, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [
                        self.binary,
                        "scan",
                        "--config",
                        self.config,
                        "--json",
                        "--quiet",
                        "--metrics=off",
                        str(target),
                    ],
                    capture_output=True,
                    timeout=SCAN_TIMEOUT * 3,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("semgrep failed on %s: %s", file.path, exc)
                return []
        try:
            results = json.loads(proc.stdout or b"{}").get("results", [])
        except json.JSONDecodeError:
            return []
        findings = []
        for item in results:
            extra = item.get("extra") or {}
            check = str(item.get("check_id") or "semgrep").rsplit(".", 1)[-1]
            findings.append(
                Finding(
                    path=file.path,
                    line=max(1, int((item.get("start") or {}).get("line") or 1)),
                    severity=SEMGREP_SEVERITY.get(str(extra.get("severity")).upper(), Severity.minor),
                    category=Category.security
                    if "security" in str(extra.get("metadata", {}).get("category", "security"))
                    else Category.bug,
                    title=f"Semgrep: {check.replace('-', ' ')}"[:200],
                    message=str(extra.get("message") or check)[:2000],
                    source=f"rule:semgrep-{check}"[:120],
                )
            )
        return _on_added_lines(file, findings)


# --------------------------------------------------------------------------- setup

SCANNERS: dict[str, Callable[[], Scanner]] = {
    "bandit": BanditScanner,
    "gitleaks": GitleaksScanner,
    "semgrep": SemgrepScanner,
}
DEFAULT_SCANNERS = "bandit,gitleaks"


def make_scanners(names: str | None = None) -> list[Scanner]:
    """Scanners named in ``names`` (or REPOSCOUT_SCANNERS) that are installed here."""
    spec = names if names is not None else os.environ.get("REPOSCOUT_SCANNERS", DEFAULT_SCANNERS)
    scanners: list[Scanner] = []
    for name in (n.strip().lower() for n in spec.split(",")):
        if not name or name == "none":
            continue
        factory = SCANNERS.get(name)
        if factory is None:
            log.warning("Unknown scanner '%s' (known: %s)", name, ", ".join(SCANNERS))
            continue
        scanner = factory()
        if scanner.available():  # type: ignore[attr-defined]
            scanners.append(scanner)
        else:
            log.info("Scanner %s is not installed; skipping it", name)
    return scanners


def run_scanners(scanners: list[Scanner], file: FileDiff, load_source: SourceLoader | None) -> list[Finding]:
    """Every scanner's findings for one file; a failing scanner never fails the review."""
    if not scanners:
        return []
    source = None
    if load_source is not None and any(s.wants_source(file.path) for s in scanners):
        try:
            source = load_source(file.path)
        except Exception as exc:
            log.warning("Could not load %s for scanning: %s", file.path, exc)
        if source is not None and len(source.encode("utf-8", "replace")) > MAX_SOURCE_BYTES:
            source = None
    findings: list[Finding] = []
    for scanner in scanners:
        try:
            findings.extend(scanner.scan(file, source))
        except Exception as exc:
            log.warning("%s failed on %s: %s", scanner.name, file.path, exc)
    return findings


def working_tree_loader(root: Path) -> SourceLoader:
    """Loads files from a local checkout (the CLI reviewing a diff of the working tree)."""
    root = root.resolve()

    def load(path: str) -> str | None:
        candidate = root / path
        if candidate.is_symlink():  # never follow a link out of the checkout
            return None
        target = candidate.resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        return target.read_text(encoding="utf-8", errors="replace")

    return load
