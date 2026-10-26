"""Settings for the public web app (all read from REPOSCOUT_ environment variables)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"REPOSCOUT_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(f"REPOSCOUT_{name}")
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(f"REPOSCOUT_{name}")
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class PublicSettings:
    # Where cloned repositories (and, without a database, their indexes) are kept.
    data_dir: Path = field(
        default_factory=lambda: Path(_env("DATA_DIR", str(Path.home() / ".reposcout-data"))).resolve()
    )
    # Postgres with pgvector (e.g. Neon's free tier) for the registry and every index. Use
    # it on hosts whose disk is wiped on restart, such as Render's free plan. Empty = files.
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    )

    # The chat API visitors' keys are sent to. Fixed on the server so visitors can't point
    # the server at arbitrary URLs.
    llm_base_url: str = field(
        default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    )
    default_model: str = field(default_factory=lambda: _env("PUBLIC_DEFAULT_MODEL", "openai/gpt-oss-20b"))
    llm_provider_name: str = field(default_factory=lambda: _env("PUBLIC_PROVIDER_NAME", "Groq"))
    llm_key_url: str = field(default_factory=lambda: _env("PUBLIC_KEY_URL", "https://console.groq.com/keys"))

    # Optional server-side GitHub token (read-only, public repos). Without it GitHub
    # allows only 60 API requests per hour from the server's IP address.
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    github_api_url: str = field(
        default_factory=lambda: os.environ.get("GITHUB_API_URL", "https://api.github.com")
    )

    # Optional "Sign in with GitHub": a GitHub OAuth App's client ID and secret (create one
    # at github.com/settings/developers; callback URL <public_url>/auth/github/callback).
    github_client_id: str = field(default_factory=lambda: _env("GITHUB_CLIENT_ID", ""))
    github_client_secret: str = field(default_factory=lambda: _env("GITHUB_CLIENT_SECRET", ""))
    # The address people use to reach the app, e.g. https://reposcout.onrender.com. Used for
    # the sign-in callback; when empty it is taken from each request.
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL", "").rstrip("/"))
    # Mark cookies Secure (HTTPS only). Defaults to on when public_url is https.
    cookie_secure: bool = field(
        default_factory=lambda: _env_bool("COOKIE_SECURE", _env("PUBLIC_URL", "").startswith("https://"))
    )
    session_days: int = field(default_factory=lambda: _env_int("SESSION_DAYS", 30))

    # Limits that keep a small server healthy.
    max_repo_mb: int = field(default_factory=lambda: _env_int("MAX_REPO_MB", 80))
    max_files: int = field(default_factory=lambda: _env_int("MAX_FILES", 3000))
    max_chunks: int = field(default_factory=lambda: _env_int("MAX_CHUNKS", 12000))
    max_repos: int = field(default_factory=lambda: _env_int("MAX_REPOS", 30))
    # Chunks kept across all repositories (0 = no limit). One chunk takes ~4 KB in Postgres
    # with 768-dimension vectors, so 60,000 fits Neon's free 0.5 GB with room to spare.
    max_total_chunks: int = field(default_factory=lambda: _env_int("MAX_TOTAL_CHUNKS", 60000))
    reindex_after_hours: int = field(default_factory=lambda: _env_int("REINDEX_AFTER_HOURS", 12))
    max_pr_files: int = field(default_factory=lambda: _env_int("MAX_PR_FILES", 40))
    max_pr_lines: int = field(default_factory=lambda: _env_int("MAX_PR_LINES", 4000))
    clone_timeout: int = field(default_factory=lambda: _env_int("CLONE_TIMEOUT", 180))
    index_timeout_minutes: int = field(default_factory=lambda: _env_int("INDEX_TIMEOUT_MINUTES", 20))
    max_queued: int = field(default_factory=lambda: _env_int("MAX_QUEUED", 8))

    # Where rate-limit counters live: "memory://" for one server, "redis://host:6379" to
    # share them across servers (needs the redis package).
    rate_limit_storage: str = field(default_factory=lambda: _env("RATE_LIMIT_STORAGE", "memory://"))
    # Requests allowed per visitor (by IP address).
    index_per_hour: int = field(default_factory=lambda: _env_int("LIMIT_INDEX_PER_HOUR", 6))
    llm_per_10min: int = field(default_factory=lambda: _env_int("LIMIT_LLM_PER_10MIN", 40))
    search_per_10min: int = field(default_factory=lambda: _env_int("LIMIT_SEARCH_PER_10MIN", 120))
    # Requests allowed to run at the same time, across all visitors. Extra requests get a
    # friendly "busy, try again" instead of piling up on the server's threads.
    max_concurrent_llm: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_LLM", 4))
    max_concurrent_search: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_SEARCH", 4))

    # Set when running behind a reverse proxy (Caddy, nginx) so the visitor's real IP comes
    # from the X-Real-IP header the proxy sets. Never enable it when the app is exposed directly.
    trust_proxy: bool = field(default_factory=lambda: _env_bool("TRUST_PROXY", False))

    # Record visitors' LLM calls in Langfuse (needs LANGFUSE_* keys). Off by default: the
    # prompts contain visitors' questions.
    trace_public: bool = field(default_factory=lambda: _env_bool("TRACE_PUBLIC", False))

    top_k: int = field(default_factory=lambda: _env_int("TOP_K", 6))
    max_context_chars: int = field(default_factory=lambda: _env_int("MAX_CONTEXT_CHARS", 16000))
    review_workers: int = field(default_factory=lambda: _env_int("REVIEW_WORKERS", 1))
    request_timeout: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT", 120))
