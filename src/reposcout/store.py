"""Index storage in a single SQLite file.

* ``chunks`` holds the code text, its location and its embedding vector.
* ``chunks_fts`` is an FTS5 full-text index over the same chunks (BM25 keyword search).
* ``chunks_vec`` is a sqlite-vec index over the vectors, when the extension is available;
  otherwise vectors are searched with one NumPy matrix product.
* ``refs`` lists the names each chunk calls (the symbol graph: "who calls X?").
* ``files`` remembers each file's content hash, so re-indexing only touches changed files.

Search is *hybrid*: vector (semantic) results and keyword results are merged with
Reciprocal Rank Fusion, which needs no score calibration between the two systems.
``BaseStore`` holds that logic; ``pg_store.PgIndexStore`` implements the same interface on
Postgres (pgvector + tsvector) for the public app.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chunking import Chunk
from .embeddings import code_tokens
from .errors import IndexMismatchError
from .paths import ranking_weight

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT,
    language TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_name ON chunks(name);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(body, tokenize = 'porter unicode61');
CREATE TABLE IF NOT EXISTS refs (chunk_id INTEGER NOT NULL, name TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_refs_name ON refs(name);
CREATE INDEX IF NOT EXISTS idx_refs_chunk ON refs(chunk_id);
"""

log = logging.getLogger(__name__)

VECTOR_BACKENDS = ("auto", "sqlite-vec", "numpy")
CHUNK_COLUMNS = "id, path, start_line, end_line, kind, name, language, text"

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "we",
    "you",
    "our",
    "my",
    "me",
    "there",
    "get",
}

RRF_K = 60
SCHEMA_VERSION = "1"  # bump when SCHEMA changes; old indexes are dropped and rebuilt
# sqlite-vec's KNN query returns at most this many rows.
VEC_MAX_K = 4096


@dataclass
class SearchResult:
    chunk_id: int
    path: str
    start_line: int
    end_line: int
    kind: str
    name: str | None
    language: str
    text: str
    score: float  # fused RRF score (higher is better)
    vector_score: float | None = None
    keyword_rank: int | None = None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


class BaseStore:
    """Hybrid search on top of four primitives each backend implements."""

    def get_meta(self, key: str) -> str | None:
        raise NotImplementedError

    def set_meta(self, key: str, value: str) -> None:
        raise NotImplementedError

    def vector_search(self, query_vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        raise NotImplementedError

    def keyword_search(self, query: str, k: int) -> list[int]:
        raise NotImplementedError

    def get_chunks(self, ids: list[int]) -> dict[int, Mapping]:
        raise NotImplementedError

    # Indexing and lookups, used by the indexer, the assistant and the reviewer.

    def close(self) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def file_hashes(self) -> dict[str, str]:
        raise NotImplementedError

    def replace_file(self, path: str, sha256: str, chunks: list[Chunk], vectors: np.ndarray) -> None:
        raise NotImplementedError

    def remove_file(self, path: str) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def chunks_overlapping(self, path: str, start: int, end: int) -> list[SearchResult]:
        raise NotImplementedError

    def find_symbol(self, name: str, limit: int = 3) -> list[SearchResult]:
        raise NotImplementedError

    def find_references(
        self, name: str, limit: int = 5, exclude_paths: set[str] | None = None
    ) -> list[SearchResult]:
        raise NotImplementedError

    def check_embedder(self, embedder_name: str) -> None:
        """Refuse to mix vectors from different embedding models in one index."""
        stored = self.get_meta("embedder")
        if stored is None:
            self.set_meta("embedder", embedder_name)
        elif stored != embedder_name:
            raise IndexMismatchError(
                f"This index was built with '{stored}' but the configured embedder is "
                f"'{embedder_name}'. Re-index with `reposcout index --rebuild`."
            )

    def hybrid_search(
        self,
        query: str,
        query_vector: np.ndarray,
        k: int = 8,
        candidates: int = 50,
        exclude_paths: set[str] | None = None,
        source_first: bool = True,
    ) -> list[SearchResult]:
        vector_hits = self.vector_search(query_vector, candidates)
        keyword_hits = self.keyword_search(query, candidates)

        fused: dict[int, float] = {}
        vector_scores: dict[int, float] = {}
        keyword_ranks: dict[int, int] = {}
        for rank, (cid, score) in enumerate(vector_hits):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            vector_scores[cid] = score
        for rank, cid in enumerate(keyword_hits):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            keyword_ranks[cid] = rank + 1

        rows = self.get_chunks(list(fused))
        scored: list[tuple[float, int]] = []
        for cid, score in fused.items():
            row = rows.get(cid)
            if row is None or (exclude_paths and row["path"] in exclude_paths):
                continue
            weight = ranking_weight(row["path"], row["language"], query) if source_first else 1.0
            scored.append((score * weight, cid))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            _to_result(rows[cid], score, vector_scores.get(cid), keyword_ranks.get(cid))
            for score, cid in scored[:k]
        ]


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension into ``conn``; False if it isn't installed or this
    Python's sqlite3 can't load extensions (e.g. some macOS builds)."""
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        return True
    except (ImportError, AttributeError, sqlite3.Error) as exc:
        log.debug("sqlite-vec unavailable: %s", exc)
        return False


class IndexStore(BaseStore):
    def __init__(self, path: Path | str, vector_backend: str | None = None):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            ignore = self.path.parent / ".gitignore"
            if not ignore.exists():  # keep the index out of the user's commits
                ignore.write_text("*\n", encoding="utf-8")
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        backend = (vector_backend or os.environ.get("REPOSCOUT_VECTOR_BACKEND") or "auto").lower()
        if backend not in VECTOR_BACKENDS:
            raise ValueError(f"Unknown vector backend '{backend}' (use {', '.join(VECTOR_BACKENDS)})")
        self.use_vec = backend != "numpy" and _load_sqlite_vec(self.conn)
        if backend == "sqlite-vec" and not self.use_vec:
            raise RuntimeError(
                "REPOSCOUT_VECTOR_BACKEND=sqlite-vec but the sqlite-vec extension can't be loaded"
            )
        self.conn.executescript(SCHEMA)
        if self.get_meta("schema_version") not in (None, SCHEMA_VERSION):
            self._drop_vec_table()
            self.conn.executescript(
                "DROP TABLE IF EXISTS chunks_fts; DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS refs; "
                "DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS meta;"
            )
            self.conn.executescript(SCHEMA)
        self.set_meta("schema_version", SCHEMA_VERSION)
        self._matrix: np.ndarray | None = None
        self._matrix_ids: np.ndarray | None = None
        self._sync_vec_table()

    @property
    def vector_backend(self) -> str:
        return "sqlite-vec" if self.use_vec else "numpy"

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ metadata

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ sqlite-vec

    def _has_vec_table(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_vec'").fetchone()
        return row is not None

    def _drop_vec_table(self) -> None:
        if self._has_vec_table():
            if not self.use_vec:  # the vec0 module isn't loaded, so the table can't be touched
                return
            self.conn.execute("DROP TABLE chunks_vec")
        self.conn.execute("DELETE FROM meta WHERE key IN ('vector_dim', 'vec_stale')")

    def _create_vec_table(self, dim: int) -> None:
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{int(dim)}] distance_metric=cosine)"
        )
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES ('vector_dim', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(int(dim)),),
        )

    def _sync_vec_table(self) -> None:
        """Keep ``chunks_vec`` in step with ``chunks``.

        Without the extension, writes can't reach ``chunks_vec``, so it's marked stale and
        rebuilt from the stored vectors the next time the extension is available."""
        with self._lock, self.conn:
            if not self.use_vec:
                if self._has_vec_table():
                    self.conn.execute(
                        "INSERT INTO meta(key, value) VALUES ('vec_stale', '1') "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                    )
                return
            if self.get_meta("vec_stale") == "1":
                self._drop_vec_table()
            if self._has_vec_table():
                return
            first = self.conn.execute("SELECT embedding FROM chunks LIMIT 1").fetchone()
            if first is None:
                return
            self._create_vec_table(len(first["embedding"]) // 4)
            self.conn.execute("INSERT INTO chunks_vec(rowid, embedding) SELECT id, embedding FROM chunks")

    def clear(self) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM chunks")
            self.conn.execute("DELETE FROM chunks_fts")
            self.conn.execute("DELETE FROM refs")
            self.conn.execute("DELETE FROM files")
            self._drop_vec_table()
            self.conn.execute("DELETE FROM meta WHERE key NOT IN ('schema_version', 'vec_stale')")
        self._invalidate()

    # ------------------------------------------------------------------ files

    def file_hashes(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT path, sha256 FROM files").fetchall()
        return {r["path"]: r["sha256"] for r in rows}

    def replace_file(self, path: str, sha256: str, chunks: list[Chunk], vectors: np.ndarray) -> None:
        """Atomically swap a file's old chunks for new ones."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        with self._lock, self.conn:
            self._delete_file_rows(path)
            for chunk, vector in zip(chunks, vectors, strict=True):
                blob = np.asarray(vector, dtype=np.float32).tobytes()
                cur = self.conn.execute(
                    "INSERT INTO chunks(path, start_line, end_line, kind, name, language, text, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.kind,
                        chunk.name,
                        chunk.language,
                        chunk.text,
                        blob,
                    ),
                )
                self.conn.execute(
                    "INSERT INTO chunks_fts(rowid, body) VALUES (?, ?)",
                    (cur.lastrowid, _fts_body(chunk)),
                )
                self.conn.executemany(
                    "INSERT INTO refs(chunk_id, name) VALUES (?, ?)",
                    [(cur.lastrowid, name) for name in chunk.refs],
                )
                if self.use_vec:
                    if not self._has_vec_table():
                        self._create_vec_table(len(blob) // 4)
                    self.conn.execute(
                        "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)", (cur.lastrowid, blob)
                    )
            self.conn.execute(
                "INSERT INTO files(path, sha256, indexed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, indexed_at = excluded.indexed_at",
                (path, sha256, time.time()),
            )
        self._invalidate()

    def remove_file(self, path: str) -> None:
        with self._lock, self.conn:
            self._delete_file_rows(path)
            self.conn.execute("DELETE FROM files WHERE path = ?", (path,))
        self._invalidate()

    def _delete_file_rows(self, path: str) -> None:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM chunks WHERE path = ?", (path,))]
        if ids:
            marks = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM refs WHERE chunk_id IN ({marks})", ids)
            if self.use_vec and self._has_vec_table():
                self.conn.execute(f"DELETE FROM chunks_vec WHERE rowid IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM chunks WHERE id IN ({marks})", ids)

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        langs = self.conn.execute(
            "SELECT language, COUNT(DISTINCT path) AS n FROM chunks GROUP BY language ORDER BY n DESC"
        ).fetchall()
        return {
            "files": files,
            "chunks": chunks,
            "languages": {r["language"]: r["n"] for r in langs},
            "embedder": self.get_meta("embedder"),
            "last_indexed": self.get_meta("last_indexed"),
            "vector_backend": self.vector_backend,
        }

    # ------------------------------------------------------------------ search

    def _invalidate(self) -> None:
        self._matrix = None
        self._matrix_ids = None

    def _load_matrix(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if self._matrix is None:
                rows = self.conn.execute("SELECT id, embedding FROM chunks ORDER BY id").fetchall()
                if rows:
                    self._matrix_ids = np.array([r["id"] for r in rows], dtype=np.int64)
                    self._matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
                else:
                    self._matrix_ids = np.zeros(0, dtype=np.int64)
                    self._matrix = np.zeros((0, 1), dtype=np.float32)
            assert self._matrix_ids is not None
            return self._matrix, self._matrix_ids

    def vector_search(self, query_vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.use_vec:
            if not self._has_vec_table():
                return []
            query = np.asarray(query_vector, dtype=np.float32)
            rows = self.conn.execute(
                "SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (query.tobytes(), min(k, VEC_MAX_K)),
            ).fetchall()
            # Cosine distance -> the same cosine similarity the NumPy path returns.
            return [(int(r[0]), 1.0 - float(r[1])) for r in rows]
        matrix, ids = self._load_matrix()
        if len(ids) == 0:
            return []
        scores = matrix @ np.asarray(query_vector, dtype=np.float32)
        k = min(k, len(ids))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(ids[i]), float(scores[i])) for i in top]

    def keyword_search(self, query: str, k: int) -> list[int]:
        terms = [t for t in dict.fromkeys(code_tokens(query)) if t not in STOPWORDS and len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
            (match, k),
        ).fetchall()
        return [r[0] for r in rows]

    def get_chunks(self, ids: list[int]) -> dict[int, Mapping]:
        out: dict[int, Mapping] = {}
        for i in range(0, len(ids), 500):
            batch = ids[i : i + 500]
            marks = ",".join("?" * len(batch))
            for row in self.conn.execute(
                f"SELECT id, path, start_line, end_line, kind, name, language, text "
                f"FROM chunks WHERE id IN ({marks})",
                batch,
            ):
                out[row["id"]] = row
        return out

    def chunks_overlapping(self, path: str, start: int, end: int) -> list[SearchResult]:
        rows = self.conn.execute(
            "SELECT id, path, start_line, end_line, kind, name, language, text FROM chunks "
            "WHERE path = ? AND start_line <= ? AND end_line >= ? ORDER BY start_line",
            (path, end, start),
        ).fetchall()
        return [_to_result(r, 0.0) for r in rows]

    def find_symbol(self, name: str, limit: int = 3) -> list[SearchResult]:
        """Find definitions named ``name`` (functions, classes, or ``Class.method``)."""
        rows = self.conn.execute(
            "SELECT id, path, start_line, end_line, kind, name, language, text FROM chunks "
            "WHERE name = ? OR name LIKE ? ORDER BY length(text) LIMIT ?",
            (name, f"%.{name}", limit),
        ).fetchall()
        return [_to_result(r, 0.0) for r in rows]

    def find_references(
        self, name: str, limit: int = 5, exclude_paths: set[str] | None = None
    ) -> list[SearchResult]:
        """Chunks that call ``name`` (the symbol graph's reverse edges), smallest first."""
        rows = self.conn.execute(
            "SELECT DISTINCT c.id, c.path, c.start_line, c.end_line, c.kind, c.name, c.language, c.text "
            "FROM refs r JOIN chunks c ON c.id = r.chunk_id WHERE r.name = ? ORDER BY length(c.text) LIMIT ?",
            (name, limit + len(exclude_paths or ())),
        ).fetchall()
        results = [_to_result(r, 0.0) for r in rows if not (exclude_paths and r["path"] in exclude_paths)]
        return results[:limit]


def _fts_body(chunk: Chunk) -> str:
    # Index split identifiers too, so "user id" matches getUserId and get_user_id.
    extra = " ".join(code_tokens(f"{chunk.path} {chunk.name or ''} {chunk.text}"))
    return f"{chunk.path}\n{chunk.name or ''}\n{extra}"


def _to_result(
    row: Mapping, score: float, vector_score: float | None = None, keyword_rank: int | None = None
) -> SearchResult:
    return SearchResult(
        chunk_id=row["id"],
        path=row["path"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        kind=row["kind"],
        name=row["name"],
        language=row["language"],
        text=row["text"],
        score=score,
        vector_score=vector_score,
        keyword_rank=keyword_rank,
    )
