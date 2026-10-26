"""The Postgres store must behave like the SQLite one. Needs REPOSCOUT_TEST_DATABASE_URL."""

import numpy as np
import pytest

from reposcout.embeddings import HashingEmbedder
from reposcout.errors import IndexMismatchError
from reposcout.indexer import Indexer
from reposcout.store import IndexStore


@pytest.fixture
def pg_indexed(pg_db, sample_repo):
    from reposcout.pg_store import PgIndexStore

    store = PgIndexStore(pg_db, pg_db.create_index("test"))
    embedder = HashingEmbedder()
    Indexer(store, embedder).index(sample_repo)
    return store, embedder


def test_uses_halfvec_on_recent_pgvector(pg_db):
    assert pg_db.vec_type == "halfvec"


def test_index_and_hybrid_search(pg_indexed):
    store, embedder = pg_indexed
    stats = store.stats()
    assert stats["files"] == 4 and stats["chunks"] > 4 and stats["vector_backend"] == "pgvector"
    query = "how is the session token signature verified"
    results = store.hybrid_search(query, embedder.embed([query])[0], k=3)
    assert results[0].path == "auth/session.py" and results[0].name == "verify_token"


def test_same_results_as_sqlite(pg_indexed, sample_repo, tmp_path):
    store, embedder = pg_indexed
    lite = IndexStore(tmp_path / "i.db")
    Indexer(lite, embedder).index(sample_repo)
    for query in ("calculate the invoice total", "remove product from cart", "verify token"):
        vector = embedder.embed([query])[0]
        pg = [(r.path, r.name) for r in store.hybrid_search(query, vector, k=3)]
        sq = [(r.path, r.name) for r in lite.hybrid_search(query, vector, k=3)]
        assert pg[0] == sq[0], query


def test_vector_scores_are_cosine_similarity(pg_indexed):
    store, embedder = pg_indexed
    query = embedder.embed(["session token"])[0]
    hits = store.vector_search(query, 3)
    assert len(hits) == 3 and hits[0][1] >= hits[1][1] >= hits[2][1]
    assert -1.0 <= hits[-1][1] <= hits[0][1] <= 1.0001  # halfvec rounding


def test_keyword_search_stems_and_splits_identifiers(pg_indexed):
    store, _ = pg_indexed
    ids = store.keyword_search("calculate totals", 5)
    rows = store.get_chunks(ids)
    assert any(rows[i]["name"] == "calculate_total" for i in ids)
    assert store.keyword_search("the of and", 5) == []
    assert store.keyword_search("'); DROP TABLE rs_chunks; --", 5) is not None  # quoted safely


def test_symbols_and_references(pg_indexed):
    store, _ = pg_indexed
    assert store.find_symbol("verify_token")[0].path == "auth/session.py"
    assert store.find_symbol("calculate_total")[0].path == "billing/invoice.py"
    assert store.find_symbol("100%_like") == []  # LIKE wildcards are escaped
    assert [c.name for c in store.find_references("compare_digest")] == ["verify_token"]
    assert store.find_references("compare_digest", exclude_paths={"auth/session.py"}) == []
    assert store.chunks_overlapping("auth/session.py", 5, 5)[0].name == "verify_token"


def test_incremental_reindex_and_removal(pg_indexed, sample_repo):
    store, embedder = pg_indexed
    before = store.stats()["chunks"]
    stats = Indexer(store, embedder).index(sample_repo)
    assert stats.files_indexed == 0 and stats.files_unchanged == 4
    (sample_repo / "auth" / "session.py").unlink()
    stats = Indexer(store, embedder).index(sample_repo)
    assert stats.files_removed == 1 and store.stats()["chunks"] < before
    assert store.find_references("compare_digest") == []  # refs went with their chunks


def test_embedder_mismatch_and_clear(pg_indexed):
    store, embedder = pg_indexed
    with pytest.raises(IndexMismatchError):
        store.check_embedder("other-model")
    store.clear()
    assert store.stats()["chunks"] == 0 and store.get_meta("embedder") is None
    assert store.vector_search(embedder.embed(["x"])[0], 3) == []


def test_generations_are_isolated_and_drop_cleanly(pg_db, pg_indexed):
    from reposcout.pg_store import PgIndexStore

    store, embedder = pg_indexed
    other = PgIndexStore(pg_db, pg_db.create_index("other"))
    assert other.stats()["chunks"] == 0
    assert other.vector_search(embedder.embed(["token"])[0], 5) == []
    pg_db.drop_index(store.index_id)
    assert not pg_db.index_exists(store.index_id)
    with pg_db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM rs_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM rs_refs").fetchone()[0] == 0


def test_text_with_nul_bytes_is_stored(pg_db):
    from reposcout.chunking import Chunk
    from reposcout.pg_store import PgIndexStore

    store = PgIndexStore(pg_db, pg_db.create_index("nul"))
    chunk = Chunk("a.txt", 1, 1, "block", None, "text", "a\0b")
    store.replace_file("a.txt", "h", [chunk], np.ones((1, 8), dtype=np.float32))
    assert store.stats()["chunks"] == 1
