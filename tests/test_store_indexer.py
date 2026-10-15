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
