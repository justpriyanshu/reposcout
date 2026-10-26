"""Second-stage reranking: a cross-encoder model re-orders the top hybrid-search results.

Hybrid search (BM25 + embeddings, fused with RRF) is cheap but scores the query and each
chunk separately. A reranker reads the query and a chunk *together*, which judges relevance
much better, so it is run on the top ~30 candidates only. Measure the effect with
``reposcout eval retrieval``.

Providers: ``jina`` (hosted, free tokens; the same API shape as Cohere's ``/rerank``).
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from .config import Settings
from .errors import ProviderError, RepoScoutError
from .store import SearchResult

log = logging.getLogger(__name__)

MAX_DOCUMENT_CHARS = 2000  # the start of a chunk carries its name and signature


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """One relevance score per document (higher is more relevant)."""
        ...


class JinaReranker:
    BASE_URL = "https://api.jina.ai/v1"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 30,
        client: httpx.Client | None = None,
    ):
        if not api_key:
            raise RepoScoutError(
                "REPOSCOUT_RERANK_PROVIDER=jina needs REPOSCOUT_JINA_API_KEY (or JINA_API_KEY)."
            )
        self.model = model
        self.name = f"jina:{model}"
        self.url = f"{(base_url or self.BASE_URL).rstrip('/')}/rerank"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.client = client or httpx.Client(timeout=timeout)

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
            "return_documents": False,
        }
        try:
            resp = self.client.post(self.url, json=payload, headers=self.headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach the reranker at {self.url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"Reranker error {resp.status_code}: {resp.text[:300]}")
        scores = [float("-inf")] * len(documents)
        for item in resp.json().get("results", []):
            index = int(item["index"])
            if 0 <= index < len(documents):
                scores[index] = float(item["relevance_score"])
        return scores


def document_text(result: SearchResult) -> str:
    header = f"File: {result.path}" + (f"\n{result.kind.capitalize()}: {result.name}" if result.name else "")
    return f"{header}\n\n{result.text[:MAX_DOCUMENT_CHARS]}"


def apply_reranker(
    reranker: Reranker | None, query: str, results: list[SearchResult], k: int
) -> list[SearchResult]:
    """Re-order ``results`` by the reranker and keep ``k``. If the reranker fails, the
    fused order is kept: reranking improves search, it must never break it."""
    if reranker is None or len(results) <= 1:
        return results[:k]
    try:
        scores = reranker.rerank(query, [document_text(r) for r in results])
    except Exception as exc:
        log.warning("Reranking failed (%s); using the hybrid search order", exc)
        return results[:k]
    order = sorted(range(len(results)), key=lambda i: (-scores[i], i))
    return [results[i] for i in order[:k]]


def make_reranker(settings: Settings) -> Reranker | None:
    provider = settings.rerank_provider.lower()
    if provider in ("", "none", "off"):
        return None
    if provider == "jina":
        return JinaReranker(
            settings.rerank_model, settings.jina_api_key, timeout=min(30, settings.request_timeout)
        )
    raise RepoScoutError(f"Unknown rerank provider '{settings.rerank_provider}' (use none or jina)")
