"""Answer questions about the indexed repository (retrieval-augmented generation)."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from .embeddings import Embedder
from .errors import IndexMismatchError
from .llm import LLM
from .rerank import Reranker, apply_reranker
from .store import BaseStore, SearchResult

QA_SYSTEM_PROMPT = """You are a senior software engineer answering questions about a codebase.
Rules:
- Use ONLY the numbered code snippets provided. Do not invent files, functions or behaviour.
- Cite the snippets you rely on inline, like [1] or [2][3].
- Mention file paths and function names so the reader can find the code.
- If the snippets do not contain the answer, say clearly that you could not find it in the
  indexed code, and suggest what to search for instead.
- Be concise: a short explanation, then a small code excerpt only if it helps.
- For follow-up questions, use the earlier conversation to work out what "it" or "that"
  refers to, but answer from the snippets."""

HISTORY_TURNS = 2
HISTORY_ANSWER_CHARS = 1200

NO_RESULTS_ANSWER = (
    "I couldn't find anything relevant in the indexed repository. "
    "Try different keywords, or re-index if the code changed recently."
)


@dataclass
class Source:
    ref: int
    path: str
    start_line: int
    end_line: int
    name: str | None
    kind: str
    score: float


@dataclass
class Answer:
    question: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    cited: list[int] = field(default_factory=list)
    latency_ms: int = 0
    used_llm: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def format_snippets(results: list[SearchResult], max_chars: int) -> tuple[str, list[SearchResult]]:
    """Number the snippets and stop before the context budget is exceeded."""
    blocks: list[str] = []
    used: list[SearchResult] = []
    total = 0
    for result in results:
        label = f"[{len(used) + 1}] {result.location}"
        if result.name:
            label += f" ({result.kind} {result.name})"
        block = f"{label}\n```{result.language}\n{result.text}\n```"
        if used and total + len(block) > max_chars:
            break
        blocks.append(block[:max_chars])
        used.append(result)
        total += len(block)
    return "\n\n".join(blocks), used


class RepoAssistant:
    def __init__(
        self,
        store: BaseStore,
        embedder: Embedder,
        llm: LLM | None,
        top_k: int = 8,
        max_context_chars: int = 24_000,
        repo_name: str | None = None,
        reranker: Reranker | None = None,
        rerank_candidates: int = 30,
    ):
        self.repo_name = repo_name
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.top_k = top_k
        self.max_context_chars = max_context_chars

    def search(
        self, query: str, k: int | None = None, exclude_paths: set[str] | None = None
    ) -> list[SearchResult]:
        stored = self.store.get_meta("embedder")
        if stored is None:
            return []  # nothing indexed yet
        if stored != self.embedder.name:
            raise IndexMismatchError(
                f"The index was built with '{stored}' but the configured embedder is "
                f"'{self.embedder.name}'. Re-index with `reposcout index --rebuild`."
            )
        vector = self.embedder.embed([query])[0]
        k = k or self.top_k
        if self.reranker is None:
            return self.store.hybrid_search(query, vector, k=k, exclude_paths=exclude_paths)
        candidates = self.store.hybrid_search(
            query, vector, k=max(k, self.rerank_candidates), exclude_paths=exclude_paths
        )
        return apply_reranker(self.reranker, query, candidates, k)

    def ask(self, question: str, k: int | None = None, history: list[dict] | None = None) -> Answer:
        """Answer ``question``. ``history`` holds earlier turns as
        ``{"question": ..., "answer": ...}`` dicts, oldest first, for follow-up questions."""
        started = time.perf_counter()
        question = question.strip()
        turns = [t for t in (history or []) if t.get("question")][-HISTORY_TURNS:]
        # A follow-up like "where is it called?" retrieves badly on its own, so search
        # with the previous question too.
        query = f"{turns[-1]['question']} {question}" if turns else question
        results = self.search(query, k)
        if not results:
            return Answer(question, NO_RESULTS_ANSWER, latency_ms=_ms(started), used_llm=False)

        context, used = format_snippets(results, self.max_context_chars)
        sources = [
            Source(i + 1, r.path, r.start_line, r.end_line, r.name, r.kind, round(r.score, 4))
            for i, r in enumerate(used)
        ]
        if self.llm is None:
            listing = "\n".join(
                f"[{s.ref}] {s.path}:{s.start_line}-{s.end_line}" + (f"  {s.name}" if s.name else "")
                for s in sources
            )
            return Answer(
                question,
                f"Most relevant code (no LLM configured):\n{listing}",
                sources,
                latency_ms=_ms(started),
                used_llm=False,
            )

        parts = []
        if self.repo_name:
            parts.append(f"Repository: {self.repo_name}")
        parts.append(f"Code snippets from the repository:\n\n{context}")
        if turns:
            earlier = "\n\n".join(
                f"Q: {t['question']}\nA: {str(t.get('answer', ''))[:HISTORY_ANSWER_CHARS]}" for t in turns
            )
            parts.append(f"Earlier in this conversation:\n{earlier}")
        parts.append(f"Question: {question}")
        prompt = "\n\n".join(parts)
        text = self.llm.complete(QA_SYSTEM_PROMPT, prompt, temperature=0.1).strip()
        cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(sources)})
        return Answer(question, text, sources, cited, latency_ms=_ms(started))


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
