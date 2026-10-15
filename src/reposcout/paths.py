"""Path classification shared by indexing, search ranking and review."""

from __future__ import annotations

import re

_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|spec|__tests__|fixtures?|mocks?|examples?|evals?)/"
    r"|(?:^|/)test_[^/]*$|_test\.\w+$|\.(?:test|spec)\.\w+$",
    re.I,
)
_TEST_QUERY = re.compile(r"\b(?:tests?|testing|spec|specs|pytest|unittest|fixtures?)\b", re.I)
_DOC_QUERY = re.compile(r"\b(?:readme|docs?|documentation|guide|install|setup)\b", re.I)

TEST_WEIGHT = 0.5
DOC_WEIGHT = 0.6
DOC_LANGUAGES = {"markdown", "text"}


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def ranking_weight(path: str, language: str, query: str) -> float:
    """Implementation code usually answers "how does X work" better than tests or docs that
    mention X, so those are down-weighted unless the question is about tests or docs."""
    if is_test_path(path) and not _TEST_QUERY.search(query):
        return TEST_WEIGHT
    if language in DOC_LANGUAGES and not _DOC_QUERY.search(query):
        return DOC_WEIGHT
    return 1.0
