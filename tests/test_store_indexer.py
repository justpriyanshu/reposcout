import numpy as np
import pytest

from reposcout.embeddings import HashingEmbedder, code_tokens, split_identifier
from reposcout.errors import IndexMismatchError
from reposcout.indexer import Indexer
from reposcout.store import IndexStore


def test_split_identifier_and_tokens():
    assert split_identifier("getUserByID") == ["get", "user", "by", "id"]
    assert split_identifier("parse_http_header") == ["parse", "http", "header"]
    assert "calculate" in code_tokens("calculateTotal(x)")


def test_hashing_embedder_is_normalised_and_deterministic():
    emb = HashingEmbedder(dim=256)
    a = emb.embed(["calculate invoice total", "calculate invoice total"])
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
    assert np.allclose(a[0], a[1])
    similar = emb.embed(["invoice total tax", "verify session token signature"])
    query = emb.embed(["total of the invoice"])[0]
    assert similar[0] @ query > similar[1] @ query


def test_index_and_hybrid_search(indexed):
    store, embedder = indexed
    stats = store.stats()
    assert stats["files"] == 4 and stats["chunks"] > 4
    query = "how is the session token signature verified"
    results = store.hybrid_search(query, embedder.embed([query])[0], k=3)
    assert results[0].path == "auth/session.py"
    assert results[0].name == "verify_token"


def test_keyword_search_matches_split_identifiers_and_stems(indexed):
    store, _ = indexed
    ids = store.keyword_search("calculate totals", 5)  # "totals" stems to "total"
    rows = store.get_chunks(ids)
    assert any(rows[i]["name"] == "calculate_total" for i in ids)


def test_incremental_reindex_only_touches_changed_files(sample_repo, settings):
    store = IndexStore(settings.index_path)
    indexer = Indexer(store, HashingEmbedder())
    first = indexer.index(sample_repo)
    assert first.files_indexed == 4

    second = indexer.index(sample_repo)
    assert second.files_indexed == 0 and second.files_unchanged == 4

    (sample_repo / "auth" / "session.py").write_text("def login():\n    return True\n")
    (sample_repo / "web" / "cart.js").unlink()
    third = indexer.index(sample_repo)
    assert third.files_indexed == 1
    assert third.files_removed == 1
    assert store.find_symbol("verify_token") == []
    assert store.find_symbol("login")[0].path == "auth/session.py"
    assert store.chunks_overlapping("web/cart.js", 1, 100) == []


def test_ignore_rules_binary_and_size_limits(sample_repo, settings):
    (sample_repo / ".reposcoutignore").write_text("secret_dir/\n")
    (sample_repo / "secret_dir").mkdir()
    (sample_repo / "secret_dir" / "hidden.py").write_text("def hidden():\n    pass\n")
    (sample_repo / "node_modules").mkdir()
    (sample_repo / "node_modules" / "dep.js").write_text("function dep() {}\n")
    (sample_repo / "blob.dat").write_bytes(b"\x00\x01binary")
    (sample_repo / "huge.py").write_text("x = 1\n" * 50_000)
    store = IndexStore(settings.index_path)
    Indexer(store, HashingEmbedder(), max_file_bytes=100_000).index(sample_repo)
    paths = set(store.file_hashes())
    assert "secret_dir/hidden.py" not in paths
    assert "node_modules/dep.js" not in paths
    assert "huge.py" not in paths
    assert "blob.dat" not in paths
    assert "billing/invoice.py" in paths


def test_index_refuses_to_mix_embedders(sample_repo, settings):
    store = IndexStore(settings.index_path)
    Indexer(store, HashingEmbedder(dim=128)).index(sample_repo)
    with pytest.raises(IndexMismatchError):
        Indexer(store, HashingEmbedder(dim=256)).index(sample_repo)
    stats = Indexer(store, HashingEmbedder(dim=256)).index(sample_repo, rebuild=True)
    assert stats.files_indexed == 4


def test_index_directory_is_git_ignored(settings, indexed):
    assert (settings.index_path.parent / ".gitignore").read_text().strip() == "*"


def test_find_symbol(indexed):
    store, _ = indexed
    hit = store.find_symbol("InvoiceRenderer")
    assert hit and hit[0].path == "billing/invoice.py" and hit[0].kind == "class"
    assert store.find_symbol("does_not_exist") == []


def test_source_code_ranks_above_tests_and_docs(sample_repo, settings):
    tests_dir = sample_repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_session.py").write_text(
        "def test_verify_token_session_signature():\n"
        "    # verify token session signature verify token session signature\n"
        "    assert verify_token('s', 't', 'sig') is False\n"
    )
    store = IndexStore(settings.index_path)
    embedder = HashingEmbedder()
    Indexer(store, embedder).index(sample_repo)
    query = "verify token session signature"
    vector = embedder.embed([query])[0]
    assert store.hybrid_search(query, vector, k=1)[0].path == "auth/session.py"
    # When the question is about tests, tests are not penalised.
    test_query = "tests for verify token session signature"
    hit = store.hybrid_search(test_query, embedder.embed([test_query])[0], k=1)[0]
    assert hit.path == "tests/test_session.py"


def test_ranking_weight():
    from reposcout.paths import DOC_WEIGHT, TEST_WEIGHT, ranking_weight

    assert ranking_weight("tests/test_a.py", "python", "how does login work") == TEST_WEIGHT
    assert ranking_weight("tests/test_a.py", "python", "which tests cover login") == 1.0
    assert ranking_weight("README.md", "markdown", "how does login work") == DOC_WEIGHT
    assert ranking_weight("README.md", "markdown", "how do I install it") == 1.0
    assert ranking_weight("src/login.py", "python", "anything") == 1.0


def test_secret_files_are_never_indexed(sample_repo, settings):
    (sample_repo / ".env").write_text("REPOSCOUT_OPENAI_API_KEY='gsk_secret'\n")
    (sample_repo / ".env.production").write_text("DB_PASSWORD=secret\n")
    (sample_repo / ".env.example").write_text("REPOSCOUT_OPENAI_API_KEY=\n")
    (sample_repo / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    store = IndexStore(settings.index_path)
    Indexer(store, HashingEmbedder()).index(sample_repo)
    paths = set(store.file_hashes())
    assert ".env" not in paths and ".env.production" not in paths and "server.pem" not in paths
    assert ".env.example" in paths  # the template has no secrets and documents the settings


needs_vec = pytest.mark.skipif(
    not IndexStore(":memory:").use_vec, reason="sqlite-vec extension not available"
)


@needs_vec
def test_sqlite_vec_and_numpy_backends_rank_the_same(sample_repo, tmp_path):
    embedder = HashingEmbedder()
    stores = {b: IndexStore(tmp_path / f"{b}.db", vector_backend=b) for b in ("sqlite-vec", "numpy")}
    for store in stores.values():
        Indexer(store, embedder).index(sample_repo)
    assert stores["sqlite-vec"].stats()["vector_backend"] == "sqlite-vec"
    query = embedder.embed(["session token signature"])[0]
    vec_hits = stores["sqlite-vec"].vector_search(query, 5)
    np_hits = stores["numpy"].vector_search(query, 5)
    assert np.allclose([s for _, s in vec_hits], [s for _, s in np_hits], atol=1e-5)
    # Same chunks in the same order, except that chunks with equal scores may swap.
    np_scores = dict(np_hits)
    assert all(abs(np_scores.get(i, -2) - s) < 1e-5 for i, s in vec_hits if s > vec_hits[-1][1] + 1e-5)


@needs_vec
def test_vector_index_follows_file_changes(sample_repo, tmp_path):
    store = IndexStore(tmp_path / "i.db", vector_backend="sqlite-vec")
    embedder = HashingEmbedder()
    Indexer(store, embedder).index(sample_repo)
    (sample_repo / "auth" / "session.py").unlink()
    Indexer(store, embedder).index(sample_repo)
    vec_ids = {r[0] for r in store.conn.execute("SELECT rowid FROM chunks_vec")}
    chunk_ids = {r[0] for r in store.conn.execute("SELECT id FROM chunks")}
    assert vec_ids == chunk_ids
    store.clear()
    assert store.vector_search(embedder.embed(["x"])[0], 3) == []


@needs_vec
def test_index_written_without_sqlite_vec_is_rebuilt_when_it_returns(sample_repo, tmp_path):
    path = tmp_path / "i.db"
    embedder = HashingEmbedder()
    first = IndexStore(path, vector_backend="sqlite-vec")
    Indexer(first, embedder).index(sample_repo)
    first.close()
    # Opened without the extension: chunks change but chunks_vec can't be updated...
    plain = IndexStore(path, vector_backend="numpy")
    (sample_repo / "auth" / "session.py").unlink()
    Indexer(plain, embedder).index(sample_repo)
    plain.close()
    # ...so the next sqlite-vec open rebuilds it from the stored vectors.
    again = IndexStore(path, vector_backend="sqlite-vec")
    vec_ids = {r[0] for r in again.conn.execute("SELECT rowid FROM chunks_vec")}
    assert vec_ids == {r[0] for r in again.conn.execute("SELECT id FROM chunks")}


def test_find_references_returns_callers(indexed):
    store, _ = indexed
    callers = store.find_references("compare_digest")
    assert [c.name for c in callers] == ["verify_token"]
    assert store.find_references("compare_digest", exclude_paths={"auth/session.py"}) == []
    assert store.find_references("nothing_calls_this") == []
