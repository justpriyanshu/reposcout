"""Many repositories on one server: clone safely, index in the background, evict old ones.

Each repository is cloned into a temporary checkout (``data_dir/repos/<key>``), indexed,
and the checkout deleted. A registry table tracks every repository's status so the web
page can show progress, and so jobs interrupted by a restart are picked up again.

Two storage modes:

* **Files** (default): the registry is ``data_dir/workspace.db`` and each index is its own
  SQLite file in ``data_dir/indexes``.
* **Postgres** (``REPOSCOUT_DATABASE_URL``): the registry and every index live in one
  Postgres database with pgvector, e.g. Neon's free tier. Hosts with an ephemeral disk
  (Render's free plan) then keep their indexes across restarts and redeploys.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..chunking import chunk_file
from ..embeddings import Embedder
from ..errors import ProviderError, RepoScoutError
from ..indexer import Indexer, iter_repo_files
from ..store import BaseStore, IndexStore
from .accounts import Accounts
from .config import PublicSettings
from .github_public import GitHubAPIError, PublicGitHub
from .links import RepoId

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 400_000
ACTIVE = ("queued", "cloning", "indexing")
MAX_OPEN_STORES = 6  # each open index keeps its vectors in memory

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    key TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    stars INTEGER NOT NULL DEFAULT 0,
    size_kb INTEGER NOT NULL DEFAULT 0,
    files_total INTEGER NOT NULL DEFAULT 0,
    files_done INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    has_index INTEGER NOT NULL DEFAULT 0,
    index_file TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    indexed_at REAL NOT NULL DEFAULT 0,
    last_used REAL NOT NULL DEFAULT 0,
    requested_by TEXT NOT NULL DEFAULT ''
);
"""
PG_SCHEMA = SCHEMA.replace(" REAL ", " DOUBLE PRECISION ").replace("stars INTEGER", "stars BIGINT")

MIN_REFRESH_SECONDS = 3600  # a "refresh" is ignored if the index is newer than this
FAILED_TTL_SECONDS = 86400  # failed rows are forgotten after a day


class QueueFullError(RepoScoutError):
    """Too many repositories are waiting to be indexed."""


class RepoLimitError(RepoScoutError):
    """The repository is too big (or otherwise unsuitable) for this server."""


# A cloner gets (repo, destination folder, timeout) and returns the commit it checked out.
Cloner = Callable[[RepoId, Path, int], str]

GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",  # never wait for a password (private or missing repo)
    "GIT_LFS_SKIP_SMUDGE": "1",  # don't download large LFS files
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_LITERAL_PATHSPECS": "1",  # file names are data, never patterns
}
GIT_SAFE = ["-c", "core.symlinks=false", "-c", "advice.detachedHead=false", "-c", "protocol.file.allow=never"]


def _run_watched(
    cmd: list[str], cwd: Path | None, watch: Path, max_bytes: int, timeout: float, stdin: bytes | None = None
) -> subprocess.CompletedProcess:
    """Run git, killing its whole process group if it runs too long or writes too much.

    Input and error output go through temporary files rather than pipes, so a chatty git
    can never block on a full pipe while we wait for it."""
    with tempfile.TemporaryFile() as stdin_file, tempfile.TemporaryFile() as stderr_file:
        if stdin:
            stdin_file.write(stdin)
            stdin_file.seek(0)
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env={**os.environ, **GIT_ENV},
            stdin=stdin_file if stdin else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    raise RepoLimitError("Downloading the repository took too long; it may be too large.")
                if watch.exists() and _folder_size(watch) > max_bytes:
                    raise RepoLimitError(
                        f"This repository is larger than the {max_bytes // 1_000_000} MB this server can handle."
                    )
                time.sleep(0.5)
        except RepoLimitError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, 9)
            proc.wait()
            raise
        stderr_file.seek(0)
        err = stderr_file.read()
    return subprocess.CompletedProcess(cmd, proc.returncode, b"", err)


def git_clone(
    repo: RepoId,
    dest: Path,
    timeout: int,
    max_bytes: int = 160_000_000,
    max_file_bytes: int = 400_000,
    max_files: int = 20_000,
) -> str:
    """Shallow clone that never writes more than ``max_bytes``.

    1. Fetch history-less metadata without checking files out, skipping any file bigger
       than ``max_file_bytes`` on the server side (partial clone).
    2. Read every file's size from git's tree listing and refuse oversized repositories
       before anything is written to disk.
    3. Check out only regular files under the size limit (no symlinks, no submodules).
    """
    started = time.monotonic()
    clone = _run_watched(
        [
            "git",
            *GIT_SAFE,
            "clone",
            "--no-checkout",
            "--depth",
            "1",
            "--single-branch",
            "--no-tags",
            "--quiet",
            f"--filter=blob:limit={max_file_bytes}",
            repo.clone_url,
            str(dest),
        ],
        None,
        dest,
        max_bytes,
        timeout,
    )
    if clone.returncode != 0:
        err = clone.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        low = err[0].lower()
        if "not found" in low or "could not read username" in low or "authentication" in low:
            raise RepoLimitError("Repository not found. Only public GitHub repositories are supported.")
        raise RepoScoutError(f"git clone failed: {err[0][:200]}")

    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-l", "-z", "HEAD"],
        cwd=dest,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
        timeout=60,
    )
    wanted: list[str] = []
    total = 0
    for entry in listing.stdout.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        mode, kind, _sha, size = meta.split()[:4]
        if kind != b"blob" or mode not in (b"100644", b"100755") or size == b"-":
            continue  # skip symlinks (120000), submodules and anything unusual
        if int(size) > max_file_bytes:
            continue
        total += int(size)
        wanted.append(path.decode("utf-8", "surrogateescape"))
    if len(wanted) > max_files:
        raise RepoLimitError(f"This repository has {len(wanted):,} files, more than this server handles.")
    if total > max_bytes:
        raise RepoLimitError(
            f"This repository is larger than the {max_bytes // 1_000_000} MB this server can handle."
        )
    if wanted:
        remaining = max(30.0, timeout - (time.monotonic() - started))
        checkout = _run_watched(
            ["git", *GIT_SAFE, "checkout", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul"],
            dest,
            dest,
            max_bytes,
            remaining,
            stdin=b"\0".join(p.encode("utf-8", "surrogateescape") for p in wanted),
        )
        if checkout.returncode != 0:
            err = checkout.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
            raise RepoScoutError(f"git checkout failed: {err[0][:200]}")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True, timeout=30)
    return head.stdout.strip()


def _folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, name)).st_size
    return total


class IndexBackend(Protocol):
    """Where indexes live. ``begin`` returns a store plus a build token that ``install``
    turns into the reference saved in the registry (``index_file``)."""

    def begin(self, key: str) -> tuple[BaseStore, Any]: ...
    def abort(self, token: Any) -> None: ...
    def install(self, key: str, token: Any) -> str: ...
    def open(self, ref: str) -> BaseStore | None: ...
    def drop(self, ref: str) -> None: ...
    def cleanup(self, refs: set[str]) -> None: ...


class FileIndexes:
    """Each index is a SQLite file in ``index_dir``; a reference is the file name."""

    def __init__(self, index_dir: Path):
        self.index_dir = index_dir

    def begin(self, key: str) -> tuple[BaseStore, Path]:
        tmp = self.index_dir / f"{key}.building.db"
        self.abort(tmp)
        return IndexStore(tmp), tmp

    def abort(self, token: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{token}{suffix}").unlink(missing_ok=True)

    def install(self, key: str, token: Path) -> str:
        """Move the finished index under a new name. Requests still reading the previous
        index keep their open file; it disappears once they finish."""
        name = f"{key}.{time.time_ns()}.db"
        os.replace(token, self.index_dir / name)
        return name

    def open(self, ref: str) -> BaseStore | None:
        path = self.index_dir / ref
        return IndexStore(path) if path.exists() else None

    def drop(self, ref: str) -> None:
        self.abort(self.index_dir / ref)

    def cleanup(self, refs: set[str]) -> None:
        """Delete index files no registry row points at (left by a crash)."""
        for path in self.index_dir.glob("*.db"):
            if path.name not in refs and not path.name.endswith(".building.db"):
                self.drop(path.name)


class PgIndexes:
    """Each index is a generation in Postgres; a reference is ``pg:<id>``.

    A replaced or evicted generation is *retired* rather than dropped at once, so requests
    still reading it can finish (the file backend gets this for free from open files)."""

    RETIRE_SECONDS = 60.0

    def __init__(self, db):
        self.db = db
        self._retired: list[tuple[float, int]] = []
        self._lock = threading.Lock()

    def begin(self, key: str) -> tuple[BaseStore, int]:
        from ..pg_store import PgIndexStore

        index_id = self.db.create_index(key)
        return PgIndexStore(self.db, index_id), index_id

    def abort(self, token: int) -> None:
        self.db.drop_index(token)

    def install(self, key: str, token: int) -> str:
        return f"pg:{token}"

    @staticmethod
    def _id(ref: str) -> int | None:
        return int(ref[3:]) if ref.startswith("pg:") and ref[3:].isdigit() else None

    def open(self, ref: str) -> BaseStore | None:
        from ..pg_store import PgIndexStore

        index_id = self._id(ref)
        if index_id is None or not self.db.index_exists(index_id):
            return None
        return PgIndexStore(self.db, index_id)

    def drop(self, ref: str) -> None:
        index_id = self._id(ref)
        with self._lock:
            if index_id is not None:
                self._retired.append((time.monotonic(), index_id))
        self.reap()

    def reap(self, force: bool = False) -> None:
        """Drop retired generations once nobody can still be reading them."""
        cutoff = time.monotonic() - self.RETIRE_SECONDS
        with self._lock:
            due = [i for when, i in self._retired if force or when <= cutoff]
            self._retired = [(when, i) for when, i in self._retired if i not in due]
        for index_id in due:
            self.db.drop_index(index_id)

    def cleanup(self, refs: set[str]) -> None:
        """Drop generations no registry row points at: half-built or retired ones left by a
        restart. (Assumes one app server per database, which is how this app is deployed.)"""
        keep = {self._id(r) for r in refs}
        for index_id in self.db.index_ids() - keep:
            self.db.drop_index(index_id)


class _Rows:
    def __init__(self, rows: list):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class PgRegistry:
    """Just enough of ``sqlite3.Connection`` for the registry code to run on Postgres:
    ``execute`` with ``?`` placeholders, and ``with registry:`` as one transaction."""

    def __init__(self, db):
        self.db = db
        self._local = threading.local()

    def __enter__(self):
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            pooled = self.db.connection()
            conn = pooled.__enter__()
            tx = conn.transaction()
            tx.__enter__()
            self._local.pooled, self._local.conn, self._local.tx = pooled, conn, tx
        self._local.depth = depth + 1
        return self

    def __exit__(self, *exc_info):
        self._local.depth -= 1
        if self._local.depth == 0:
            pooled, tx = self._local.pooled, self._local.tx
            self._local.conn = None
            try:
                tx.__exit__(*exc_info)
            finally:
                pooled.__exit__(*exc_info)
        return False

    def execute(self, sql: str, args: tuple = ()):
        sql = sql.replace("?", "%s")
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return _Rows(_fetch(conn.execute(sql, args)))
        with self.db.connection() as fresh:
            return _Rows(_fetch(fresh.execute(sql, args)))


def _fetch(cursor) -> list:
    return cursor.fetchall() if cursor.description else []


class Workspace:
    def __init__(
        self,
        settings: PublicSettings,
        embedder: Embedder,
        github: PublicGitHub | None = None,
        cloner: Cloner | None = None,
        db=None,
    ):
        """``db`` is a ``pg_store.PgDatabase``; by default one is opened when
        ``settings.database_url`` is set, otherwise everything is stored in files."""
        self.settings = settings
        self.embedder = embedder
        self.github = github
        self.cloner = cloner or (
            lambda repo, dest, timeout: git_clone(
                repo,
                dest,
                timeout,
                max_bytes=settings.max_repo_mb * 1_000_000 * 3 // 2,  # the checkout includes .git
                max_file_bytes=MAX_FILE_BYTES,
                max_files=max(settings.max_files * 5, 5000),
            )
        )
        self.repos_dir = settings.data_dir / "repos"
        self.index_dir = settings.data_dir / "indexes"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if db is None and settings.database_url:
            from ..pg_store import PgDatabase

            db = PgDatabase.shared(settings.database_url)
        if db is not None:
            db.ensure_schema()
        self.pg = db
        self.indexes: IndexBackend
        if db is not None:
            self._db = PgRegistry(db)
            with self._db:
                self._db.execute(PG_SCHEMA)
            self.indexes = PgIndexes(db)
            self._latest = "GREATEST"
        else:
            self._db = sqlite3.connect(str(settings.data_dir / "workspace.db"), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
            columns = {r["name"] for r in self._db.execute("PRAGMA table_info(repos)")}
            if "requested_by" not in columns:  # registries created by an older version
                self._db.execute("ALTER TABLE repos ADD COLUMN requested_by TEXT NOT NULL DEFAULT ''")
            self.indexes = FileIndexes(self.index_dir)
            self._latest = "MAX"  # SQLite's two-argument MAX is Postgres's GREATEST
        self._stores: OrderedDict[str, BaseStore] = OrderedDict()
        # Who added which repository (each person's private list) and sign-in sessions.
        self.accounts = Accounts(
            self._db, self._lock, postgres=db is not None, session_days=settings.session_days
        )
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        # Jobs that were running when the server stopped are queued again.
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT key FROM repos WHERE status IN (?, ?, ?) ORDER BY updated_at", ACTIVE
            ).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE repos SET status='queued', stage='Waiting to start' WHERE key=?", (row["key"],)
                )
                self._queue.put(row["key"])
            refs = {
                r["index_file"]
                for r in self._db.execute("SELECT index_file FROM repos WHERE index_file != ''")
            }
        self.indexes.cleanup(refs)

    # ------------------------------------------------------------------ registry

    def _row(self, key: str):
        with self._lock:
            return self._db.execute("SELECT * FROM repos WHERE key = ?", (key,)).fetchone()

    def _update(self, key: str, **fields) -> None:
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE repos SET {assignments} WHERE key = ?", (*fields.values(), key))

    def get(self, repo: RepoId) -> dict | None:
        row = self._row(repo.key)
        return self._public(row) if row else None

    def list(self, limit: int = 24) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM repos WHERE has_index = 1 OR status IN (?, ?, ?) "
                f"ORDER BY {self._latest}(last_used, updated_at) DESC LIMIT ?",
                (*ACTIVE, limit),
            ).fetchall()
        return [self._public(r) for r in rows]

    def queue_position(self, key: str) -> int:
        with self._lock:
            rows = self._db.execute(
                "SELECT key FROM repos WHERE status = 'queued' ORDER BY updated_at"
            ).fetchall()
        keys = [r["key"] for r in rows]
        return keys.index(key) + 1 if key in keys else 0

    def _public(self, row) -> dict:
        data = dict(row)
        data.pop("index_file", None)
        data.pop("requested_by", None)
        data["full_name"] = f"{row['owner']}/{row['name']}"
        data["ready"] = bool(row["has_index"])
        data["queue_position"] = self.queue_position(row["key"]) if row["status"] == "queued" else 0
        return data

    # ------------------------------------------------------------------ jobs

    def would_queue(self, repo: RepoId, force: bool = False) -> bool:
        """True if ``submit`` would start a new job (rather than return the current state)."""
        row = self._row(repo.key)
        if row is None:
            return True
        if row["status"] in ACTIVE:
            return False
        if row["status"] != "ready" or not row["has_index"]:
            return True
        age = time.time() - row["indexed_at"]
        if force:
            return age >= MIN_REFRESH_SECONDS
        return age >= self.settings.reindex_after_hours * 3600

    def active_jobs(self, requested_by: str | None = None) -> int:
        with self._lock:
            if requested_by is None:
                sql, args = "SELECT COUNT(*) FROM repos WHERE status IN (?, ?, ?)", ACTIVE
            else:
                sql = "SELECT COUNT(*) FROM repos WHERE status IN (?, ?, ?) AND requested_by = ?"
                args = (*ACTIVE, requested_by)
            row = self._db.execute(sql, args).fetchone()
            return int(row[0]) if row else 0

    def submit(self, repo: RepoId, force: bool = False, requested_by: str = "") -> dict:
        """Queue a repository for (re-)indexing unless it's fresh or already in progress.

        Raises ``QueueFullError`` when the server already has ``max_queued`` jobs waiting, or
        when this visitor already has a job in progress."""
        now = time.time()
        with self._lock, self._db:
            if not self.would_queue(repo, force):
                return self._public(self._row(repo.key))
            if self.active_jobs() >= self.settings.max_queued:
                raise QueueFullError(
                    "The server is busy indexing other repositories. Please try again in a few minutes."
                )
            if requested_by and self.active_jobs(requested_by) >= 1:
                raise QueueFullError(
                    "You already have a repository being indexed. Wait for it to finish, then try again."
                )
            self._prune_failed(now)
            if self._row(repo.key) is not None:
                self._db.execute(
                    "UPDATE repos SET status='queued', stage='Waiting to start', error='', "
                    "files_done=0, updated_at=?, requested_by=? WHERE key=?",
                    (now, requested_by, repo.key),
                )
            else:
                self._db.execute(
                    "INSERT INTO repos (key, owner, name, status, stage, created_at, updated_at, last_used, "
                    "requested_by) VALUES (?, ?, ?, 'queued', 'Waiting to start', ?, ?, ?, ?)",
                    (repo.key, repo.owner, repo.name, now, now, now, requested_by),
                )
        self._queue.put(repo.key)
        record = self.get(repo)
        assert record is not None
        return record

    def _prune_failed(self, now: float) -> None:
        self._db.execute(
            "DELETE FROM repos WHERE status = 'failed' AND has_index = 0 AND updated_at < ?",
            (now - FAILED_TTL_SECONDS,),
        )

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stopping.clear()
        self._worker = threading.Thread(target=self._work, name="reposcout-indexer", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put("")  # wake the worker

    def _work(self) -> None:
        while not self._stopping.is_set():
            key = self._queue.get()
            if key:
                self.run_job(key)

    def drain(self) -> None:
        """Run every queued job in the calling thread (used by tests and the CLI)."""
        while True:
            try:
                key = self._queue.get_nowait()
            except queue.Empty:
                return
            if key:
                self.run_job(key)

    def run_job(self, key: str) -> None:
        row = self._row(key)
        if row is None or row["status"] != "queued":
            return
        repo = RepoId(row["owner"], row["name"])
        had_index = bool(row["has_index"])
        work_dir = self.repos_dir / f"{key}.incoming"
        building = None  # the half-built index, dropped if the job fails
        try:
            self._check_metadata(repo)
            self._update(key, status="cloning", stage="Downloading code")
            shutil.rmtree(work_dir, ignore_errors=True)
            commit = self.cloner(repo, work_dir, self.settings.clone_timeout)
            size_mb = _folder_size(work_dir) / 1_000_000
            if size_mb > self.settings.max_repo_mb * 1.5:  # the checkout includes .git
                raise RepoLimitError(
                    f"This repository is {size_mb:.0f} MB, larger than the "
                    f"{self.settings.max_repo_mb} MB this server can handle."
                )
            files = self._check_size(work_dir)
            self._update(
                key, status="indexing", stage="Reading the code", files_total=len(files), files_done=0
            )
            building = self._build_index(key, work_dir)
            self._install(key, work_dir, building)
            building = None
            store = self.store(repo)
            stats = store.stats() if store else {"chunks": 0}
            self._update(
                key,
                status="ready",
                stage="Ready",
                error="",
                commit_sha=commit,
                chunks=stats["chunks"],
                has_index=1,
                indexed_at=time.time(),
                files_done=len(files),
            )
            self._evict()
        except Exception as exc:
            if not isinstance(exc, RepoScoutError):
                log.exception("Indexing %s failed", repo.full_name)
            message = str(exc) if isinstance(exc, RepoScoutError) else "Indexing failed unexpectedly."
            if isinstance(exc, ProviderError):
                message = "The embedding service is unavailable right now. Please try again shortly."
            shutil.rmtree(work_dir, ignore_errors=True)
            if building is not None:
                with contextlib.suppress(Exception):
                    self.indexes.abort(building)
            # A failed refresh keeps serving the previous index.
            status = "ready" if had_index else "failed"
            self._update(key, status=status, stage="Failed" if not had_index else "Ready", error=message)

    def _check_metadata(self, repo: RepoId) -> None:
        if self.github is None:
            return
        self._update(repo.key, status="cloning", stage="Checking the repository")
        try:
            meta = self.github.repo(repo)
        except GitHubAPIError as exc:
            if exc.status == 404:
                raise RepoLimitError(
                    "Repository not found. Only public GitHub repositories are supported."
                ) from exc
            log.warning("GitHub metadata unavailable for %s: %s", repo.full_name, exc)
            return  # the clone and size checks below still protect the server
        if meta.get("private"):
            raise RepoLimitError("Only public repositories are supported.")
        size_kb = int(meta.get("size") or 0)
        if size_kb > self.settings.max_repo_mb * 1024:
            raise RepoLimitError(
                f"This repository is about {size_kb // 1024} MB, larger than the "
                f"{self.settings.max_repo_mb} MB this server can handle."
            )
        owner, _, name = str(meta.get("full_name") or repo.full_name).partition("/")
        self._update(
            repo.key,
            owner=owner or repo.owner,
            name=name or repo.name,
            branch=meta.get("default_branch") or "",
            description=(meta.get("description") or "")[:300],
            language=meta.get("language") or "",
            stars=int(meta.get("stargazers_count") or 0),
            size_kb=size_kb,
        )

    def _check_size(self, root: Path) -> list[tuple[str, Path]]:
        files = list(iter_repo_files(root, MAX_FILE_BYTES))
        if len(files) > self.settings.max_files:
            raise RepoLimitError(
                f"This repository has {len(files):,} source files; this server indexes up to "
                f"{self.settings.max_files:,}."
            )
        if not files:
            raise RepoLimitError("No source files to index were found in this repository.")
        chunks = 0
        for rel, path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks += len(chunk_file(rel, text))
            if chunks > self.settings.max_chunks:
                raise RepoLimitError(
                    "This repository is too large to index on this server "
                    f"(more than {self.settings.max_chunks:,} code sections)."
                )
        return files

    def _build_index(self, key: str, root: Path):
        """Index ``root`` into a new, not yet visible index. Returns its build token."""
        store, token = self.indexes.begin(key)
        last = [0.0]
        deadline = time.monotonic() + self.settings.index_timeout_minutes * 60

        def progress(done: int, total: int) -> None:
            now = time.monotonic()
            if now > deadline and done < total:
                raise RepoLimitError(
                    f"Indexing took longer than {self.settings.index_timeout_minutes} minutes, the limit "
                    "on this server. Try a smaller repository."
                )
            if now - last[0] > 0.5 or done == total:
                last[0] = now
                self._update(key, files_done=done, stage=f"Indexing code ({done:,} of {total:,} files)")

        try:
            Indexer(store, self.embedder, MAX_FILE_BYTES).index(root, on_progress=progress)
        except BaseException:
            store.close()
            self.indexes.abort(token)
            raise
        store.close()
        return token

    def _index_ref(self, key: str) -> str | None:
        row = self._row(key)
        return row["index_file"] if row is not None and row["index_file"] else None

    def _install(self, key: str, work_dir: Path, token) -> None:
        """Point the registry at the new index and drop the previous one.

        The checkout isn't needed after indexing (chunks are stored in the index), so it
        is deleted to save disk."""
        old_ref = self._index_ref(key)
        with self._lock:
            new_ref = self.indexes.install(key, token)
            self._update(key, index_file=new_ref)
            self._stores.pop(key, None)
            if old_ref is not None:
                self.indexes.drop(old_ref)
        shutil.rmtree(work_dir, ignore_errors=True)

    def store(self, repo: RepoId) -> BaseStore | None:
        with self._lock:
            store = self._stores.get(repo.key)
            if store is not None:
                self._stores.move_to_end(repo.key)
                return store
            ref = self._index_ref(repo.key)
            store = self.indexes.open(ref) if ref else None
            if store is None:
                return None
            self._stores[repo.key] = store
            while len(self._stores) > MAX_OPEN_STORES:
                # Drop the least recently used. Its connection closes once no request uses it.
                self._stores.popitem(last=False)
            return store

    def touch(self, repo: RepoId) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE repos SET last_used = ? WHERE key = ?", (time.time(), repo.key))

    def _evict(self) -> None:
        """Keep at most ``max_repos`` indexed repositories and ``max_total_chunks`` chunks
        in all (a free database has little storage); drop the least recently used. The
        most recent repository is always kept."""
        with self._lock:
            rows = self._db.execute(
                "SELECT key, chunks FROM repos WHERE has_index = 1 AND status = 'ready' "
                f"ORDER BY {self._latest}(last_used, indexed_at) DESC"
            ).fetchall()
            total = 0
            budget = self.settings.max_total_chunks
            for position, row in enumerate(rows):
                total += int(row["chunks"])
                over_budget = budget > 0 and total > budget and position > 0
                if position >= self.settings.max_repos or over_budget:
                    self._delete(row["key"])

    def _delete(self, key: str) -> None:
        with self._lock:
            self._stores.pop(key, None)
            ref = self._index_ref(key)
            if ref is not None:
                self.indexes.drop(ref)
            with self._db:
                self._db.execute("DELETE FROM repos WHERE key = ?", (key,))
            log.info("Evicted %s", key)
