"""Fast, deterministic checks that run on every added line before (and without) the LLM.

Rules catch the mechanical problems (secrets, debug leftovers, risky calls) reliably and
for free; the LLM is left to reason about logic and design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..chunking import detect_language
from ..paths import is_test_path
from .diff import FileDiff
from .models import Category, Finding, Severity


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    severity: Severity
    category: Category
    title: str
    message: str
    languages: frozenset[str] | None = None  # None = every language
    skip_tests: bool = False


def _rule(id, pattern, severity, category, title, message, languages=None, skip_tests=False, flags=0):
    return Rule(
        id,
        re.compile(pattern, flags),
        Severity(severity),
        Category(category),
        title,
        message,
        frozenset(languages) if languages else None,
        skip_tests,
    )


RULES: list[Rule] = [
    _rule(
        "private-key",
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        "critical",
        "security",
        "Private key committed",
        "A private key is being added to the repository. Remove it, rotate the key, and load it from a secret store instead.",
    ),
    _rule(
        "aws-key",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        "critical",
        "security",
        "AWS access key committed",
        "This looks like an AWS access key ID. Remove it and rotate the credentials.",
    ),
    _rule(
        "hardcoded-secret",
        r"""(?i)\b[\w-]*(?:api[_-]?key|secret|passwd|password|token|auth)[\w-]*["']?\s*[:=]\s*["'][^"'\s$<>{}]{8,}["']""",
        "major",
        "security",
        "Possible hard-coded secret",
        "A credential-like value is written directly in code. Read it from an environment variable or secret manager.",
        skip_tests=True,
    ),
    _rule(
        "python-eval",
        r"(?<![\w.])(?:eval|exec)\s*\(",
        "major",
        "security",
        "Use of eval/exec",
        "eval/exec runs arbitrary code. If the input can be influenced by users this is remote code execution. Prefer ast.literal_eval or explicit parsing.",
        languages={"python"},
    ),
    _rule(
        "shell-true",
        r"subprocess\.\w+\(.*shell\s*=\s*True",
        "major",
        "security",
        "subprocess with shell=True",
        "shell=True passes the command through a shell, which allows injection if any part is user-controlled. Pass a list of arguments instead.",
        languages={"python"},
    ),
    _rule(
        "tls-verify-off",
        r"verify\s*=\s*False",
        "major",
        "security",
        "TLS certificate verification disabled",
        "verify=False disables certificate checks and allows man-in-the-middle attacks.",
        languages={"python"},
    ),
    _rule(
        "sql-format",
        r"""(?i)\.execute\(\s*(?:f["']|["'][^"']*\b(?:select|insert|update|delete)\b[^"']*["']\s*(?:%|\+|\.format))""",
        "major",
        "security",
        "SQL built with string formatting",
        "Building SQL with f-strings, % or + allows SQL injection. Use parameterised queries (placeholders plus a parameters tuple).",
        languages={"python"},
    ),
    _rule(
        "yaml-load",
        r"yaml\.load\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)",
        "major",
        "security",
        "Unsafe yaml.load",
        "yaml.load can construct arbitrary objects. Use yaml.safe_load.",
        languages={"python"},
    ),
    _rule(
        "pickle-load",
        r"pickle\.loads?\(",
        "minor",
        "security",
        "pickle on possibly untrusted data",
        "Unpickling untrusted data can execute code. Make sure this input is trusted, or use JSON.",
        languages={"python"},
    ),
    _rule(
        "bare-except",
        r"^\s*except\s*:",
        "minor",
        "bug",
        "Bare except",
        "A bare except also catches KeyboardInterrupt and SystemExit and hides real errors. Catch specific exceptions (or at least Exception).",
        languages={"python"},
    ),
    _rule(
        "mutable-default",
        r"def \w+\(.*=\s*(?:\[\]|\{\}|set\(\))",
        "minor",
        "bug",
        "Mutable default argument",
        "Default values are created once and shared between calls. Use None and create the list/dict inside the function.",
        languages={"python"},
    ),
    _rule(
        "breakpoint",
        r"(?:^|\s)(?:breakpoint\(\)|import pdb|pdb\.set_trace\(\)|debugger;)",
        "major",
        "bug",
        "Debugger statement left in",
        "This will pause execution in production. Remove it before merging.",
    ),
    _rule(
        "console-log",
        r"\bconsole\.(?:log|debug)\(",
        "nit",
        "style",
        "console.log left in",
        "Debug logging left in the change. Remove it or use the project's logger.",
        languages={"javascript", "typescript"},
        skip_tests=True,
    ),
    _rule(
        "js-eval",
        r"(?<![\w.])eval\s*\(",
        "major",
        "security",
        "Use of eval",
        "eval runs arbitrary code; avoid it, especially with user input.",
        languages={"javascript", "typescript"},
    ),
    _rule(
        "inner-html",
        r"\.innerHTML\s*=(?!=)",
        "minor",
        "security",
        "Assignment to innerHTML",
        "Setting innerHTML with dynamic content can cause XSS. Use textContent or sanitise the HTML.",
        languages={"javascript", "typescript"},
    ),
    _rule(
        "todo",
        r"(?:#|//|/\*)\s*(?:TODO|FIXME|XXX|HACK)\b",
        "nit",
        "maintainability",
        "TODO added",
        "A TODO/FIXME is being introduced. Consider opening an issue so it isn't forgotten.",
    ),
]


def run_rules(file: FileDiff) -> list[Finding]:
    language = detect_language(file.path)
    test_file = is_test_path(file.path)
    findings: list[Finding] = []
    for lineno, content in file.added_lines():
        for rule in RULES:
            if rule.languages and language not in rule.languages:
                continue
            if rule.skip_tests and test_file:
                continue
            if rule.pattern.search(content):
                findings.append(
                    Finding(
                        path=file.path,
                        line=lineno,
                        severity=rule.severity,
                        category=rule.category,
                        title=rule.title,
                        message=rule.message,
                        source=f"rule:{rule.id}",
                    )
                )
    return findings
