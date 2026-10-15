"""Settings, read from environment variables (all prefixed REPOSCOUT_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"REPOSCOUT_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(f"REPOSCOUT_{name}")
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # Which repository to index, and where the index lives.
    repo_path: Path = field(default_factory=lambda: Path(_env("REPO_PATH", ".")).resolve())
    index_dir_name: str = ".reposcout"

    # Embeddings: "ollama" (local, free), "openai" (any OpenAI-compatible API) or
    # "hash" (no model at all, lexical only; good for CI and tests).
    embed_provider: str = field(default_factory=lambda: _env("EMBED_PROVIDER", "ollama"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "nomic-embed-text"))

    # Chat model: "ollama" or "openai" (OpenAI-compatible: Groq, Gemini, LM Studio, vLLM...).
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "ollama"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "qwen2.5-coder:7b"))

    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    openai_base_url: str = field(
        default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    )
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))

    # GitHub integration.
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    github_webhook_secret: str = field(default_factory=lambda: _env("WEBHOOK_SECRET", ""))
    github_api_url: str = field(
        default_factory=lambda: os.environ.get("GITHUB_API_URL", "https://api.github.com")
    )

    # Tuning knobs.
    max_file_bytes: int = field(default_factory=lambda: _env_int("MAX_FILE_BYTES", 400_000))
    chunk_max_lines: int = field(default_factory=lambda: _env_int("CHUNK_MAX_LINES", 80))
    top_k: int = field(default_factory=lambda: _env_int("TOP_K", 8))
    max_context_chars: int = field(default_factory=lambda: _env_int("MAX_CONTEXT_CHARS", 24_000))
    request_timeout: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT", 180))

    @property
    def index_path(self) -> Path:
        return self.repo_path / self.index_dir_name / "index.db"

    def with_repo(self, repo_path: str | Path) -> Settings:
        return replace(self, repo_path=Path(repo_path).resolve())

    def with_overrides(self, **kwargs) -> Settings:
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean)
