"""Index storage in Postgres, for the public app (Render's disk is wiped on every restart,
so indexes live in a database such as Neon's free Postgres instead).

The same ``BaseStore`` interface as the SQLite store, on shared tables:

* ``rs_indexes``: one row per index *generation*. A refresh builds a new generation and
  the registry switches to it in one update, so readers never see a half-built index.
* ``rs_chunks``: code text plus a pgvector embedding (``halfvec`` when pgvector >= 0.7,
  half the storage of ``vector``) and a ``tsvector`` for keyword search.
* ``rs_refs``, ``rs_files``, ``rs_meta``: the symbol graph, file hashes and metadata.

Vector search is exact (``ORDER BY embedding <=> query``) within one index, which a
B-tree on ``index_id`` keeps fast at this app's size (a few thousand chunks per repo);
a global HNSW index would need filtered ANN search for no real gain here.

Requires the ``postgres`` extra: ``pip install reposcout[postgres]``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping
from typing import Any

import numpy as np

from .chunking import Chunk
from .embeddings import code_tokens
from .store import STOPWORDS, BaseStore, SearchResult, _fts_body, _to_result

log = logging.getLogger(__name__)

CHUNK_COLUMNS = "id, path, start_line, end_line, kind, name, language, text"
_TERM = re.compile(r"^[a-z0-9_]+$")


def _schema(vec_type: str) -> list[str]:
    return [
        """CREATE TABLE IF NOT EXISTS rs_indexes (
            id BIGSERIAL PRIMARY KEY,
            label TEXT NOT NULL DEFAULT '',
            created_at DOUBLE PRECISION NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS rs_meta (
            index_id BIGINT NOT NULL REFERENCES rs_indexes(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (index_id, key)
        )""",
        """CREATE TABLE IF NOT EXISTS rs_files (
            index_id BIGINT NOT NULL REFERENCES rs_indexes(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            indexed_at DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (index_id, path)
        )""",
        f"""CREATE TABLE IF NOT EXISTS rs_chunks (
            id BIGSERIAL PRIMARY KEY,
            index_id BIGINT NOT NULL REFERENCES rs_indexes(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            kind TEXT NOT NULL,
            name TEXT,
            language TEXT NOT NULL,
            text TEXT NOT NULL,
            embedding {vec_type} NOT NULL,
            fts TSVECTOR NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS rs_chunks_index_path ON rs_chunks (index_id, path)",
        "CREATE INDEX IF NOT EXISTS rs_chunks_index_name ON rs_chunks (index_id, name)",
        "CREATE INDEX IF NOT EXISTS rs_chunks_fts ON rs_chunks USING GIN (fts)",
        """CREATE TABLE IF NOT EXISTS rs_refs (
            chunk_id BIGINT NOT NULL REFERENCES rs_chunks(id) ON DELETE CASCADE,
            index_id BIGINT NOT NULL,
            name TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS rs_refs_index_name ON rs_refs (index_id, name)",
        "CREATE INDEX IF NOT EXISTS rs_refs_chunk ON rs_refs (chunk_id)",
    ]


class Row(dict):
    """A dict row that also answers ``row[0]``, like ``sqlite3.Row``."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [c.name for c in cursor.description or []]

    def make(values):
        return Row(zip(names, values, strict=True))

    return make


class PgDatabase:
    """A connection pool plus the schema. Use ``PgDatabase.shared(url)`` for one per process."""

    _shared: dict[str, PgDatabase] = {}
    _shared_lock = threading.Lock()

    @classmethod
    def shared(cls, url: str) -> PgDatabase:
        with cls._shared_lock:
            db = cls._shared.get(url)
            if db is None:
                db = cls._shared[url] = cls(url)
            return db

    def __init__(self, url: str, min_size: int = 1, max_size: int = 4):
        import psycopg
        from psycopg_pool import ConnectionPool

        self.url = url
        # The extension must exist before connections register the vector types.
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            found = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
            version = found[0] if found else "0"
        self.vec_type = "halfvec" if _version_tuple(version) >= (0, 7) else "vector"
        self.pool = ConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            # prepare_threshold=None: Neon's pooled URLs (PgBouncer) can't keep prepared statements.
            kwargs={"autocommit": True, "row_factory": _row_factory, "prepare_threshold": None},
            configure=self._configure,
            check=ConnectionPool.check_connection,
            open=True,
            name="reposcout",
        )
        self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create the tables if they don't exist (cheap; safe to call again)."""
        with self.pool.connection() as conn:
            existing = conn.execute(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = to_regclass('rs_chunks') AND attname = 'embedding'"
            ).fetchone()
            if existing:  # keep whatever type the tables were created with
                self.vec_type = "halfvec" if existing[0].startswith("halfvec") else "vector"
            with conn.transaction():
                for statement in _schema(self.vec_type):
                    conn.execute(_sql(statement))

    @staticmethod
    def _configure(conn) -> None:
        from pgvector.psycopg import register_vector

        register_vector(conn)

    def close(self) -> None:
        self.pool.close()

    def connection(self):
        return self.pool.connection()

    # ------------------------------------------------------------------ generations

    def create_index(self, label: str) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "INSERT INTO rs_indexes (label, created_at) VALUES (%s, %s) RETURNING id",
                (label, time.time()),
            ).fetchone()
        assert row is not None
        return int(row["id"])

    def drop_index(self, index_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM rs_indexes WHERE id = %s", (index_id,))

    def index_exists(self, index_id: int) -> bool:
        with self.connection() as conn:
            return conn.execute("SELECT 1 FROM rs_indexes WHERE id = %s", (index_id,)).fetchone() is not None

    def index_ids(self) -> set[int]:
        with self.connection() as conn:
            return {int(r["id"]) for r in conn.execute("SELECT id FROM rs_indexes")}


def _sql(text: str) -> Any:
    """psycopg's type hints only accept literal strings. These queries are built from
    constants (table names, the vector type), never from input."""
    return text


def _one(cursor) -> Row:
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("query returned no row")
    return row


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


class PgIndexStore(BaseStore):
    """One index generation inside a ``PgDatabase``."""

    vector_backend = "pgvector"

    def __init__(self, db: PgDatabase, index_id: int):
        self.db = db
        self.index_id = index_id
        self._lock = threading.RLock()
        self._vec = db.vec_type

    def close(self) -> None:
        """Connections belong to the shared pool; nothing to close."""

    # ------------------------------------------------------------------ metadata

    def get_meta(self, key: str) -> str | None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT value FROM rs_meta WHERE index_id = %s AND key = %s", (self.index_id, key)
            ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO rs_meta (index_id, key, value) VALUES (%s, %s, %s) "
                "ON CONFLICT (index_id, key) DO UPDATE SET value = EXCLUDED.value",
                (self.index_id, key, value),
            )

    def clear(self) -> None:
        with self.db.connection() as conn, conn.transaction():
            for table in ("rs_chunks", "rs_files", "rs_meta"):
                conn.execute(_sql(f"DELETE FROM {table} WHERE index_id = %s"), (self.index_id,))

    # ------------------------------------------------------------------ files

    def file_hashes(self) -> dict[str, str]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT path, sha256 FROM rs_files WHERE index_id = %s", (self.index_id,))
            return {r["path"]: r["sha256"] for r in rows}

    def replace_file(self, path: str, sha256: str, chunks: list[Chunk], vectors: np.ndarray) -> None:
        """Atomically swap a file's old chunks for new ones."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        with self._lock, self.db.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM rs_chunks WHERE index_id = %s AND path = %s", (self.index_id, path))
            refs: list[tuple[int, int, str]] = []
            if chunks:
                with conn.cursor() as cur:
                    cur.executemany(
                        _sql(
                            "INSERT INTO rs_chunks (index_id, path, start_line, end_line, kind, name, language, "
                            f"text, embedding, fts) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::{self._vec}, "
                            "to_tsvector('english', %s)) RETURNING id"
                        ),
                        [
                            (
                                self.index_id,
                                c.path,
                                c.start_line,
                                c.end_line,
                                c.kind,
                                c.name,
                                c.language,
                                c.text.replace("\0", ""),
                                np.asarray(v, dtype=np.float32),
                                _fts_body(c),
                            )
                            for c, v in zip(chunks, vectors, strict=True)
                        ],
                        returning=True,
                    )
                    ids = []
                    while True:
                        ids.append(int(_one(cur)["id"]))
                        if not cur.nextset():
                            break
                refs = [
                    (cid, self.index_id, name) for cid, c in zip(ids, chunks, strict=True) for name in c.refs
                ]
            if refs:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO rs_refs (chunk_id, index_id, name) VALUES (%s, %s, %s)", refs
                    )
            conn.execute(
                "INSERT INTO rs_files (index_id, path, sha256, indexed_at) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (index_id, path) DO UPDATE SET sha256 = EXCLUDED.sha256, "
                "indexed_at = EXCLUDED.indexed_at",
                (self.index_id, path, sha256, time.time()),
            )

    def remove_file(self, path: str) -> None:
        with self.db.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM rs_chunks WHERE index_id = %s AND path = %s", (self.index_id, path))
            conn.execute("DELETE FROM rs_files WHERE index_id = %s AND path = %s", (self.index_id, path))

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        with self.db.connection() as conn:
            files = _one(conn.execute("SELECT COUNT(*) FROM rs_files WHERE index_id = %s", (self.index_id,)))
            chunks = _one(
                conn.execute("SELECT COUNT(*) FROM rs_chunks WHERE index_id = %s", (self.index_id,))
            )
            langs = conn.execute(
                "SELECT language, COUNT(DISTINCT path) AS n FROM rs_chunks WHERE index_id = %s "
                "GROUP BY language ORDER BY n DESC",
                (self.index_id,),
            ).fetchall()
        return {
            "files": int(files[0]),
            "chunks": int(chunks[0]),
            "languages": {r["language"]: int(r["n"]) for r in langs},
            "embedder": self.get_meta("embedder"),
            "last_indexed": self.get_meta("last_indexed"),
            "vector_backend": self.vector_backend,
        }

    # ------------------------------------------------------------------ search

    def vector_search(self, query_vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        query = np.asarray(query_vector, dtype=np.float32)
        with self.db.connection() as conn:
            rows = conn.execute(
                _sql(
                    f"SELECT id, 1 - (embedding <=> %s::{self._vec}) AS score FROM rs_chunks "
                    f"WHERE index_id = %s ORDER BY embedding <=> %s::{self._vec} LIMIT %s"
                ),
                (query, self.index_id, query, k),
            ).fetchall()
        return [(int(r["id"]), float(r["score"])) for r in rows]

    def keyword_search(self, query: str, k: int) -> list[int]:
        terms = [
            t
            for t in dict.fromkeys(code_tokens(query))
            if t not in STOPWORDS and len(t) > 1 and _TERM.match(t)
        ]
        if not terms:
            return []
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT id FROM rs_chunks, to_tsquery('english', %s) AS q "
                "WHERE index_id = %s AND fts @@ q ORDER BY ts_rank_cd(fts, q) DESC, id LIMIT %s",
                (" | ".join(terms), self.index_id, k),
            ).fetchall()
        return [int(r["id"]) for r in rows]

    def get_chunks(self, ids: list[int]) -> dict[int, Mapping]:
        if not ids:
            return {}
        with self.db.connection() as conn:
            rows = conn.execute(
                _sql(f"SELECT {CHUNK_COLUMNS} FROM rs_chunks WHERE index_id = %s AND id = ANY(%s)"),
                (self.index_id, list(ids)),
            ).fetchall()
        return {int(r["id"]): r for r in rows}

    def chunks_overlapping(self, path: str, start: int, end: int) -> list[SearchResult]:
        with self.db.connection() as conn:
            rows = conn.execute(
                _sql(
                    f"SELECT {CHUNK_COLUMNS} FROM rs_chunks WHERE index_id = %s AND path = %s "
                    "AND start_line <= %s AND end_line >= %s ORDER BY start_line"
                ),
                (self.index_id, path, end, start),
            ).fetchall()
        return [_to_result(r, 0.0) for r in rows]

    def find_symbol(self, name: str, limit: int = 3) -> list[SearchResult]:
        """Find definitions named ``name`` (functions, classes, or ``Class.method``)."""
        with self.db.connection() as conn:
            rows = conn.execute(
                _sql(
                    f"SELECT {CHUNK_COLUMNS} FROM rs_chunks WHERE index_id = %s AND (name = %s OR name LIKE %s) "
                    "ORDER BY length(text) LIMIT %s"
                ),
                (self.index_id, name, f"%.{_escape_like(name)}", limit),
            ).fetchall()
        return [_to_result(r, 0.0) for r in rows]

    def find_references(
        self, name: str, limit: int = 5, exclude_paths: set[str] | None = None
    ) -> list[SearchResult]:
        """Chunks that call ``name`` (the symbol graph's reverse edges), smallest first."""
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT c.id, c.path, c.start_line, c.end_line, c.kind, c.name, c.language, c.text, "
                "length(c.text) AS size FROM rs_refs r JOIN rs_chunks c ON c.id = r.chunk_id "
                "WHERE r.index_id = %s AND r.name = %s ORDER BY size LIMIT %s",
                (self.index_id, name, limit + len(exclude_paths or ())),
            ).fetchall()
        results = [_to_result(r, 0.0) for r in rows if not (exclude_paths and r["path"] in exclude_paths)]
        return results[:limit]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
