"""Review a diff: deterministic rules + an LLM that sees the change and related repo code."""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from pydantic import ValidationError

from ..assistant import RepoAssistant
from ..chunking import detect_language
from ..llm import LLM
from ..store import SearchResult
from .diff import FileDiff, parse_unified_diff
from .models import Finding, Severity
from .rules import run_rules

log = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """You are a meticulous senior engineer reviewing one file of a pull request.

Focus on problems that matter, in this order: bugs and incorrect logic, security issues,
missing error handling, concurrency/resource leaks, performance problems, then
maintainability. Only flag style when it hurts readability badly.

Rules:
- Only comment on ADDED lines (marked "+"). Use the exact line number printed on the left.
- "Related code" is from the rest of the repository; use it to understand the change
  (signatures, callers, conventions) but do not review it.
- Every finding must be specific and actionable. No praise, no generic advice, no
  speculation about code you cannot see.
- If the change looks correct, return an empty findings list. Fewer, accurate findings
  are better than many weak ones.
- Return at most {max_findings} findings.

Respond with JSON only, in exactly this shape:
{{"summary": "<one sentence on what this file's change does>",
  "findings": [{{"line": <int>, "severity": "critical|major|minor|nit",
                "category": "bug|security|performance|maintainability|style|testing",
                "title": "<short title>", "message": "<what is wrong and why it matters>",
                "suggestion": "<corrected code or concrete fix, optional>"}}]}}"""

SKIP_LLM_LANGUAGES = {"markdown", "text"}
GENERATED_PATTERNS = re.compile(
    r"(?:^|/)(?:vendor|node_modules|dist|build|third_party)/|\.min\.(?:js|css)$|\.lock$"
    r"|(?:^|/)(?:package-lock\.json|pnpm-lock\.yaml|yarn\.lock|poetry\.lock|go\.sum|Cargo\.lock)$"
    r"|_pb2\.py$|\.pb\.go$|\.generated\.\w+$",
)
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
NOT_SYMBOLS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "print",
    "len",
    "str",
    "int",
    "float",
    "list",
    "dict",
    "set",
    "tuple",
    "range",
    "super",
    "isinstance",
    "getattr",
    "setattr",
    "hasattr",
    "open",
    "sorted",
    "enumerate",
    "zip",
    "map",
    "filter",
    "min",
    "max",
    "sum",
    "any",
    "all",
    "type",
    "repr",
    "bool",
    "catch",
    "function",
    "require",
    "import",
    "new",
    "typeof",
    "sizeof",
    "fmt",
    "self",
    "this",
    "log",
    "append",
    "get",
    "format",
    "join",
    "split",
    "strip",
    "items",
    "keys",
    "values",
    "update",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
}
SEVERITY_ICON = {"critical": "🔴", "major": "🟠", "minor": "🟡", "nit": "⚪"}


@dataclass
class ReviewStats:
    files_total: int = 0
    llm_calls: int = 0
    rule_findings: int = 0
    llm_findings: int = 0
    dropped_invalid: int = 0  # LLM output that failed schema validation
    dropped_off_diff: int = 0  # findings pointing at lines not in the diff (hallucinated lines)
    snapped_lines: int = 0  # findings moved to the nearest added line
    duplicates_removed: int = 0
    llm_errors: int = 0
    latency_ms: int = 0

    def merge(self, other: ReviewStats) -> None:
        for name in (
            "llm_calls",
            "rule_findings",
            "llm_findings",
            "dropped_invalid",
            "dropped_off_diff",
            "snapped_lines",
            "llm_errors",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class ReviewResult:
    findings: list[Finding]
    file_summaries: dict[str, str] = field(default_factory=dict)
    files_reviewed: list[str] = field(default_factory=list)
    files_skipped: dict[str, str] = field(default_factory=dict)
    stats: ReviewStats = field(default_factory=ReviewStats)
    additions: int = 0
    deletions: int = 0
    llm_used: bool = True

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def worst(self) -> Severity | None:
        return max((f.severity for f in self.findings), key=lambda s: s.rank, default=None)

    def headline(self) -> str:
        counts = self.counts()
        parts = [f"{n} {sev}" for sev, n in counts.items() if n]
        found = ", ".join(parts) if parts else "no issues"
        return f"Reviewed {len(self.files_reviewed)} file(s) (+{self.additions}/-{self.deletions}): {found}."

    def to_dict(self) -> dict:
        return {
            "headline": self.headline(),
            "counts": self.counts(),
            "findings": [f.model_dump(mode="json") for f in self.findings],
            "file_summaries": self.file_summaries,
            "files_reviewed": self.files_reviewed,
            "files_skipped": self.files_skipped,
            "stats": self.stats.__dict__,
            "llm_used": self.llm_used,
        }

    def to_markdown(self, include_findings: bool = True) -> str:
        lines = ["## 🔍 RepoScout review", "", self.headline()]
        if not self.llm_used:
            lines += ["", "_Rules-only review (no LLM configured)._"]
        if self.file_summaries:
            lines += ["", "**What changed**", ""]
            lines += [f"- `{p}`: {s}" for p, s in self.file_summaries.items() if s]
        if include_findings and self.findings:
            lines += ["", "| Severity | Location | Issue |", "|---|---|---|"]
            for f in self.findings:
                icon = SEVERITY_ICON[f.severity.value]
                title = f.title.replace("|", "\\|")
                lines.append(f"| {icon} {f.severity.value} | `{f.path}:{f.line}` | {title} |")
            lines.append("")
            for f in self.findings:
                lines += [
                    f"<details><summary><code>{f.path}:{f.line}</code> {f.title}</summary>",
                    "",
                    comment_body(f),
                    "",
                    "</details>",
                ]
        if self.files_skipped:
            skipped = ", ".join(f"`{p}` ({why})" for p, why in self.files_skipped.items())
            lines += ["", f"Skipped: {skipped}"]
        return "\n".join(lines).strip() + "\n"

    def to_text(self) -> str:
        out = [self.headline()]
        for path, summary in self.file_summaries.items():
            if summary:
                out.append(f"  {path}: {summary}")
        if self.findings:
            out.append("")
        for f in self.findings:
            out.append(
                f"[{f.severity.value.upper()}] {f.path}:{f.line}  {f.title}  ({f.category.value}, {f.source})"
            )
            out.append(f"    {f.message}")
            if f.suggestion:
                for s_line in f.suggestion.splitlines():
                    out.append(f"    > {s_line}")
        if self.files_skipped:
            out.append("")
            out.append("Skipped: " + ", ".join(f"{p} ({w})" for p, w in self.files_skipped.items()))
        return "\n".join(out)


def comment_body(finding: Finding) -> str:
    icon = SEVERITY_ICON[finding.severity.value]
    body = (
        f"{icon} **{finding.severity.value.capitalize()} · {finding.category.value}**: "
        f"{finding.title}\n\n{finding.message}"
    )
    if finding.suggestion:
        lang = detect_language(finding.path)
        fence = lang if lang not in ("text", "config") else ""
        body += f"\n\n**Suggested fix**\n```{fence}\n{finding.suggestion}\n```"
    if finding.source.startswith("rule:"):
        body += f"\n\n<sub>rule `{finding.source[5:]}`</sub>"
    return body


def extract_json(text: str):
    """Parse JSON from a model reply, tolerating code fences and surrounding prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


class Reviewer:
    def __init__(
        self,
        llm: LLM | None,
        assistant: RepoAssistant | None = None,
        *,
        max_files: int = 25,
        max_findings_per_file: int = 6,
        min_severity: Severity = Severity.nit,
        max_diff_chars: int = 12_000,
        context_snippets: int = 3,
        workers: int = 4,
    ):
        self.llm = llm
        self.assistant = assistant
        self.max_files = max_files
        self.max_findings_per_file = max_findings_per_file
        self.min_severity = min_severity
        self.max_diff_chars = max_diff_chars
        self.context_snippets = context_snippets
        self.workers = workers

    # ------------------------------------------------------------------ public

    def review(self, diff_text: str, pr_title: str = "", pr_body: str = "") -> ReviewResult:
        return self.review_files(parse_unified_diff(diff_text), pr_title, pr_body)

    def review_files(self, files: list[FileDiff], pr_title: str = "", pr_body: str = "") -> ReviewResult:
        started = time.perf_counter()
        result = ReviewResult(findings=[], llm_used=self.llm is not None)
        stats = result.stats
        stats.files_total = len(files)

        to_review: list[FileDiff] = []
        for file in files:
            reason = self._skip_reason(file)
            if reason:
                result.files_skipped[file.path] = reason
            elif len(to_review) >= self.max_files:
                result.files_skipped[file.path] = "file limit reached"
            else:
                to_review.append(file)
            result.additions += file.additions
            result.deletions += file.deletions

        def work(file: FileDiff) -> tuple[FileDiff, list[Finding], str, ReviewStats]:
            # Each worker counts into its own stats object; they are merged afterwards.
            local = ReviewStats()
            findings = run_rules(file)
            local.rule_findings += len(findings)
            summary = ""
            if self.llm is not None and detect_language(file.path) not in SKIP_LLM_LANGUAGES:
                llm_findings, summary = self._llm_review(file, pr_title, pr_body, local)
                local.llm_findings += len(llm_findings)
                findings += llm_findings
            return file, findings, summary, local

        if self.workers > 1 and len(to_review) > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                outputs = list(pool.map(work, to_review))
        else:
            outputs = [work(f) for f in to_review]

        for file, findings, summary, local in outputs:
            stats.merge(local)
            result.files_reviewed.append(file.path)
            if summary:
                result.file_summaries[file.path] = summary
            result.findings.extend(self._finalise(findings, stats))

        result.findings.sort(key=lambda f: (-f.severity.rank, f.path, f.line))
        stats.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ------------------------------------------------------------------ internals

    def _skip_reason(self, file: FileDiff) -> str | None:
        if file.is_deleted:
            return "deleted"
        if file.is_binary:
            return "binary"
        if GENERATED_PATTERNS.search(file.path):
            return "generated or vendored"
        if not file.added_lines():
            return "no added lines"
        return None

    def _llm_review(
        self, file: FileDiff, pr_title: str, pr_body: str, stats: ReviewStats
    ) -> tuple[list[Finding], str]:
        diff_text = file.annotated()
        truncated = len(diff_text) > self.max_diff_chars
        if truncated:
            diff_text = diff_text[: self.max_diff_chars] + "\n... (diff truncated)"

        parts = []
        if pr_title:
            parts.append(f"Pull request: {pr_title}")
            if pr_body:
                parts.append(f"Description: {pr_body[:1500]}")
        context = self._related_context(file)
        if context:
            parts.append(f"Related code (context only, do not review):\n\n{context}")
        status = "new file" if file.is_new else ("renamed" if file.is_rename else "modified")
        parts.append(
            f"File under review: {file.path} ({detect_language(file.path)}, {status})\n"
            f"Diff (new-file line number, marker, code):\n```\n{diff_text}\n```"
        )
        system = REVIEW_SYSTEM_PROMPT.format(max_findings=self.max_findings_per_file)
        try:
            reply = self.llm.complete(system, "\n\n".join(parts), json_mode=True, temperature=0.0)
        except Exception as exc:  # keep the rule findings even if the model fails
            log.warning("LLM review failed for %s: %s", file.path, exc)
            stats.llm_errors += 1
            return [], ""
        stats.llm_calls += 1

        data = extract_json(reply)
        if isinstance(data, list):
            data = {"summary": "", "findings": data}
        if not isinstance(data, dict):
            stats.dropped_invalid += 1
            return [], ""

        added = file.added_line_numbers()
        commentable = file.commentable_lines()
        findings: list[Finding] = []
        for raw in data.get("findings") or []:
            if not isinstance(raw, dict):
                stats.dropped_invalid += 1
                continue
            try:
                line = int(str(raw.get("line", "")).strip().split("-")[0])
                finding = Finding(**{**raw, "path": file.path, "line": line, "source": "llm"})
            except (ValueError, ValidationError, TypeError):
                stats.dropped_invalid += 1
                continue
            if finding.line not in commentable:
                nearest = min(added, key=lambda n: abs(n - finding.line), default=None)
                if nearest is None or abs(nearest - finding.line) > 3:
                    stats.dropped_off_diff += 1
                    continue
                finding.line = nearest
                stats.snapped_lines += 1
            findings.append(finding)
        summary = str(data.get("summary") or "").strip()
        return findings, summary[:300]

    def _related_context(self, file: FileDiff) -> str:
        """Pull definitions of functions the change calls, plus semantically similar code."""
        if self.assistant is None or self.context_snippets <= 0:
            return ""
        added_text = "\n".join(content for _, content in file.added_lines())
        symbols = [
            s for s in dict.fromkeys(CALL_RE.findall(added_text)) if s not in NOT_SYMBOLS and len(s) > 2
        ][:8]
        picked: dict[int, SearchResult] = {}
        try:
            for symbol in symbols:
                for hit in self.assistant.store.find_symbol(symbol, limit=1):
                    if hit.path != file.path:
                        picked.setdefault(hit.chunk_id, hit)
                if len(picked) >= self.context_snippets:
                    break
            if len(picked) < self.context_snippets:
                stem = PurePosixPath(file.path).stem
                query = f"{stem} {' '.join(symbols)} {added_text[:500]}"
                for hit in self.assistant.search(query, k=self.context_snippets, exclude_paths={file.path}):
                    picked.setdefault(hit.chunk_id, hit)
        except Exception as exc:  # context is a bonus; never fail the review over it
            log.warning("Context retrieval failed for %s: %s", file.path, exc)
            return ""

        blocks = []
        for hit in list(picked.values())[: self.context_snippets]:
            text = "\n".join(hit.text.splitlines()[:60])
            label = f"{hit.location}" + (f" ({hit.kind} {hit.name})" if hit.name else "")
            blocks.append(f"{label}\n```{hit.language}\n{text}\n```")
        return "\n\n".join(blocks)

    def _finalise(self, findings: list[Finding], stats: ReviewStats) -> list[Finding]:
        """Drop low-severity noise, merge duplicates (rules win ties), cap per file."""
        best: dict[tuple[str, int, str], Finding] = {}
        for f in findings:
            if f.severity.rank < self.min_severity.rank:
                continue
            key = (f.path, f.line, f.category.value)
            current = best.get(key)
            if current is None:
                best[key] = f
                continue
            stats.duplicates_removed += 1
            better = (f.severity.rank, f.source.startswith("rule:")) > (
                current.severity.rank,
                current.source.startswith("rule:"),
            )
            if better:
                best[key] = f
        ordered = sorted(best.values(), key=lambda f: (-f.severity.rank, f.line))
        return ordered[: self.max_findings_per_file]
