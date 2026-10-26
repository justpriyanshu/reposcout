from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from reposcout.assistant import RepoAssistant
from reposcout.config import Settings
from reposcout.embeddings import HashingEmbedder
from reposcout.indexer import Indexer
from reposcout.llm import LLMUsage
from reposcout.store import IndexStore


class FakeLLM:
    """Stands in for a real model. ``responder`` gets (system, user) and returns the reply."""

    name = "fake"

    def __init__(self, responder: Callable[[str, str], str] | str = "ok"):
        self.responder = responder
        self.calls: list[tuple[str, str, bool]] = []
        self.usage = LLMUsage()

    def complete(self, system: str, user: str, *, json_mode: bool = False, temperature: float = 0.1) -> str:
        self.calls.append((system, user, json_mode))
        self.usage.calls += 1
        if callable(self.responder):
            return self.responder(system, user)
        return self.responder


def findings_reply(findings: list[dict], summary: str = "Changes the code.") -> str:
    return json.dumps({"summary": summary, "findings": findings})


SAMPLE_FILES = {
    "billing/invoice.py": '''"""Invoice helpers."""

TAX_RATE = 0.18


def calculate_total(items, discount=0.0):
    """Sum item prices, apply a discount and add tax."""
    subtotal = sum(item.price * item.quantity for item in items)
    return round((subtotal - discount) * (1 + TAX_RATE), 2)


class InvoiceRenderer:
    """Turns invoices into printable text."""

    def render(self, invoice):
        lines = [f"Invoice #{invoice.number}"]
        for item in invoice.items:
            lines.append(f"{item.name}: {item.price}")
        return "\\n".join(lines)
''',
    "auth/session.py": '''import hashlib
import hmac


def verify_token(secret: str, token: str, signature: str) -> bool:
    """Check that a session token was signed with our secret."""
    expected = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def logout(session):
    session.clear()
''',
    "web/cart.js": """import { api } from "./api";

export function addToCart(cart, product) {
  cart.items.push(product);
  return cart;
}

export const removeFromCart = (cart, productId) => {
  cart.items = cart.items.filter((p) => p.id !== productId);
  return cart;
};
""",
    "README.md": "# Shop\n\nA tiny shop used in tests.\n\n## Billing\n\nTotals include 18% tax.\n",
}


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "shop"
    for rel, text in SAMPLE_FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


@pytest.fixture
def settings(sample_repo: Path) -> Settings:
    return (
        Settings()
        .with_repo(sample_repo)
        .with_overrides(
            embed_provider="hash",
            llm_provider="ollama",
            github_token="",
            github_webhook_secret="",
        )
    )


@pytest.fixture
def indexed(sample_repo: Path, settings: Settings):
    store = IndexStore(settings.index_path)
    embedder = HashingEmbedder()
    Indexer(store, embedder).index(sample_repo)
    return store, embedder


@pytest.fixture
def make_assistant(indexed):
    store, embedder = indexed

    def build(llm=None) -> RepoAssistant:
        return RepoAssistant(store, embedder, llm)

    return build


PG_TABLES = (
    "rs_refs",
    "rs_chunks",
    "rs_files",
    "rs_meta",
    "rs_indexes",
    "repos",
    "users",
    "sessions",
    "user_repos",
)


@pytest.fixture
def pg_url():
    """A Postgres (with pgvector) for tests; skipped unless REPOSCOUT_TEST_DATABASE_URL is set.
    Every test starts from empty tables."""
    url = os.environ.get("REPOSCOUT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set REPOSCOUT_TEST_DATABASE_URL to run the Postgres tests")
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        for table in PG_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    return url


@pytest.fixture
def pg_db(pg_url):
    from reposcout.pg_store import PgDatabase

    db = PgDatabase(pg_url, max_size=2)
    yield db
    db.close()
