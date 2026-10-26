"""Embedding providers. Every provider returns L2-normalised float32 vectors."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

import httpx
import numpy as np

from .config import Settings
from .errors import ProviderError, RepoScoutError

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_identifier(word: str) -> list[str]:
    """``getUserByID`` -> [get, user, by, id]; ``parse_http_header`` -> [parse, http, header]."""
    parts: list[str] = []
    for piece in word.split("_"):
        parts.extend(p.lower() for p in _CAMEL.findall(piece))
    return [p for p in parts if p]


def code_tokens(text: str) -> list[str]:
    """Tokens for lexical matching: whole identifiers plus their split parts."""
    tokens: list[str] = []
    for word in _IDENT.findall(text):
        lower = word.lower()
        tokens.append(lower)
        parts = split_identifier(word)
        if len(parts) > 1:
            tokens.extend(parts)
    return tokens


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class HashingEmbedder:
    """Model-free embedding using the "hashing trick" over code tokens.

    It captures lexical overlap only (no meaning), but needs no download, is fully
    deterministic and runs anywhere, which makes it ideal for tests and CI.
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _index_sign(self, token: str) -> tuple[int, float]:
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        return value % self.dim, 1.0 if (value >> 63) & 1 else -1.0

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            counts = Counter(code_tokens(text))
            for token, count in counts.items():
                idx, sign = self._index_sign(token)
                out[row, idx] += sign * (1.0 + math.log(count))
        return _normalise(out)


class OllamaEmbedder:
    """Local embeddings through Ollama (free). Example model: ``nomic-embed-text``."""

    def __init__(
        self,
        model: str,
        base_url: str,
        timeout: float = 120,
        batch_size: int = 32,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.name = f"ollama:{model}"
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.client = client or httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                resp = self.client.post(
                    f"{self.base_url}/api/embed", json={"model": self.model, "input": batch}
                )
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"Could not reach Ollama at {self.base_url} ({exc}). Is it running? "
                    "Start it with `ollama serve`, or set REPOSCOUT_EMBED_PROVIDER=hash."
                ) from exc
            if resp.status_code == 404:
                raise ProviderError(
                    f"Ollama does not have the model '{self.model}'. Run `ollama pull {self.model}`."
                )
            if resp.status_code >= 400:
                raise ProviderError(f"Ollama embedding error {resp.status_code}: {resp.text[:300]}")
            data = resp.json().get("embeddings")
            if not data or len(data) != len(batch):
                raise ProviderError("Ollama returned an unexpected embedding response.")
            vectors.extend(data)
        return _normalise(np.array(vectors, dtype=np.float32))


class OpenAICompatibleEmbedder:
    """Embeddings from any OpenAI-compatible ``/embeddings`` endpoint."""

    prefix = "openai"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 120,
        batch_size: int = 64,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.name = f"{self.prefix}:{model}"
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                resp = self.client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": self.model, "input": batch},
                    headers=self.headers,
                )
            except httpx.HTTPError as exc:
                raise ProviderError(f"Could not reach {self.base_url}: {exc}") from exc
            if resp.status_code in (401, 403):
                raise ProviderError(f"The embedding API at {self.base_url} rejected the API key.")
            if resp.status_code >= 400:
                raise ProviderError(f"Embedding API error {resp.status_code}: {resp.text[:300]}")
            items = sorted(resp.json()["data"], key=lambda d: d["index"])
            vectors.extend(item["embedding"] for item in items)
        return _normalise(np.array(vectors, dtype=np.float32))


class JinaEmbedder(OpenAICompatibleEmbedder):
    """Jina AI's hosted embeddings (OpenAI-compatible API). ``jina-embeddings-v2-base-code``
    is trained on code and natural language; a new API key gets 10M free tokens
    (non-commercial use). Get one at https://jina.ai/embeddings."""

    prefix = "jina"
    BASE_URL = "https://api.jina.ai/v1"

    def __init__(self, model: str, api_key: str, timeout: float = 120, client: httpx.Client | None = None):
        if not api_key:
            raise RepoScoutError(
                "REPOSCOUT_EMBED_PROVIDER=jina needs REPOSCOUT_JINA_API_KEY (or JINA_API_KEY)."
            )
        super().__init__(model, self.BASE_URL, api_key, timeout, batch_size=64, client=client)


# Used when REPOSCOUT_EMBED_MODEL is empty. For better code search with Ollama, try
# ``qwen3-embedding:0.6b`` (needs ``ollama pull qwen3-embedding:0.6b`` and a re-index).
DEFAULT_EMBED_MODELS = {
    "ollama": "nomic-embed-text",
    "jina": "jina-embeddings-v2-base-code",
    "openai": "nomic-embed-text",  # unchanged from earlier versions, so old indexes still match
}


def make_embedder(settings: Settings) -> Embedder:
    provider = settings.embed_provider.lower()
    model = settings.embed_model or DEFAULT_EMBED_MODELS.get(provider, "")
    if provider == "hash":
        return HashingEmbedder()
    if provider == "ollama":
        return OllamaEmbedder(model, settings.ollama_url, settings.request_timeout)
    if provider == "jina":
        return JinaEmbedder(model, settings.jina_api_key, settings.request_timeout)
    if provider == "openai":
        return OpenAICompatibleEmbedder(
            model,
            settings.openai_base_url,
            settings.openai_api_key,
            settings.request_timeout,
        )
    raise RepoScoutError(
        f"Unknown embedding provider '{settings.embed_provider}' (use ollama, jina, openai or hash)"
    )
