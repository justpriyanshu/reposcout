"""Wire the components together from Settings (used by both the CLI and the API server)."""

from __future__ import annotations

from dataclasses import dataclass

from .assistant import RepoAssistant
from .config import Settings
from .embeddings import Embedder, make_embedder
from .indexer import Indexer
from .llm import LLM, make_llm
from .review import Reviewer, Severity
from .store import IndexStore


@dataclass
class Services:
    settings: Settings
    store: IndexStore
    embedder: Embedder
    llm: LLM | None
    assistant: RepoAssistant
    reviewer: Reviewer

    def indexer(self) -> Indexer:
        return Indexer(self.store, self.embedder, self.settings.max_file_bytes, self.settings.chunk_max_lines)


def build_services(
    settings: Settings,
    *,
    use_llm: bool = True,
    min_severity: Severity = Severity.nit,
    use_context: bool = True,
) -> Services:
    store = IndexStore(settings.index_path)
    embedder = make_embedder(settings)
    llm = make_llm(settings) if use_llm else None
    assistant = RepoAssistant(store, embedder, llm, settings.top_k, settings.max_context_chars)
    reviewer = Reviewer(
        llm,
        assistant if use_context else None,
        min_severity=min_severity,
        workers=max(1, settings.review_workers),
    )
    return Services(settings, store, embedder, llm, assistant, reviewer)
