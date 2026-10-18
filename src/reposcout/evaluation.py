"""Measure retrieval and review quality on labelled datasets, so changes can be compared with numbers."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .assistant import RepoAssistant
from .review import Reviewer, Severity
from .store import SearchResult

LINE_TOLERANCE = 2


def load_cases(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) else data


def _matches(result: SearchResult, expected: str) -> bool:
    """``expected`` is ``path`` or ``path::Symbol`` (Symbol may be a class or Class.method)."""
    path, _, symbol = expected.partition("::")
    if result.path != path:
        return False
    if not symbol:
        return True
    name = result.name or ""
    return name == symbol or name.startswith(symbol + ".") or name.endswith("." + symbol)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def eval_retrieval(assistant: RepoAssistant, cases: list[dict], k: int = 5) -> dict:
    """Hit@1, Recall@k and MRR: did the right file/function come back, and how high?"""
    hits_at_1 = hits_at_k = 0
    reciprocal_ranks: list[float] = []
    latencies: list[float] = []
    misses: list[dict] = []

    for case in cases:
        started = time.perf_counter()
        results = assistant.search(case["question"], k=k)
        latencies.append((time.perf_counter() - started) * 1000)
        rank = next(
            (i + 1 for i, r in enumerate(results) if any(_matches(r, e) for e in case["expected"])),
            None,
        )
        if rank == 1:
            hits_at_1 += 1
        if rank is not None:
            hits_at_k += 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)
            misses.append(
                {
                    "question": case["question"],
                    "expected": case["expected"],
                    "got": [f"{r.path}::{r.name}" if r.name else r.path for r in results[:3]],
                }
            )

    n = len(cases) or 1
    return {
        "cases": len(cases),
        "k": k,
        "hit@1": round(hits_at_1 / n, 3),
        f"recall@{k}": round(hits_at_k / n, 3),
        "mrr": round(statistics.fmean(reciprocal_ranks) if reciprocal_ranks else 0.0, 3),
        "latency_ms_p50": round(_percentile(latencies, 0.5), 1),
        "latency_ms_p95": round(_percentile(latencies, 0.95), 1),
        "misses": misses,
    }


def eval_review(reviewer: Reviewer, cases: list[dict]) -> dict:
    """Precision/recall of review findings against seeded, labelled issues.

    A finding matches an expected issue when it is in the same file and within
    LINE_TOLERANCE lines. Cases with no expected issues measure false alarms on clean code.
    """
    tp = fp = fn = 0
    clean_cases = clean_with_alarms = 0
    latencies: list[float] = []
    details: list[dict] = []

    for case in cases:
        result = reviewer.review(case["diff"])
        latencies.append(result.stats.latency_ms)
        # Nits are style opinions; they are excluded so they neither help nor hurt the score.
        findings = [f for f in result.findings if f.severity.rank >= Severity.minor.rank]
        expected = case["expected"]
        matched_findings: set[int] = set()
        case_tp = 0
        for exp in expected:
            hit = next(
                (
                    i
                    for i, f in enumerate(findings)
                    if i not in matched_findings
                    and f.path == exp["path"]
                    and abs(f.line - exp["line"]) <= LINE_TOLERANCE
                ),
                None,
            )
            if hit is not None:
                matched_findings.add(hit)
                case_tp += 1
        case_fp = len(findings) - len(matched_findings)
        case_fn = len(expected) - case_tp
        tp, fp, fn = tp + case_tp, fp + case_fp, fn + case_fn
        if not expected:
            clean_cases += 1
            clean_with_alarms += int(bool(findings))
        details.append(
            {
                "name": case["name"],
                "expected": len(expected),
                "found": case_tp,
                "false_positives": case_fp,
                "findings": [
                    f"{f.path}:{f.line} [{f.severity.value}] {f.title} ({f.source})" for f in findings
                ],
            }
        )

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "cases": len(cases),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "clean_case_false_alarm_rate": round(clean_with_alarms / clean_cases, 3) if clean_cases else None,
        "latency_ms_p50": round(_percentile(latencies, 0.5), 1),
        "details": details,
    }
