"""Many repositories on one server: clone safely, index in the background, evict old ones.

Each repository gets its own checkout (``data_dir/repos/<key>``) and its own index file
(``data_dir/indexes/<key>.db``). A small SQLite registry tracks their status so the web
page can show progress, and so jobs interrupted by a restart are picked up again.
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

from ..chunking import chunk_file
from ..embeddings import Embedder
from ..errors import ProviderError, RepoScoutError
from ..indexer import Indexer, iter_repo_files
from ..store import IndexStore
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


class Workspace:
    def __init__(
        self,
        settings: PublicSettings,
        embedder: Embedder,
        github: PublicGitHub | None = None,
        cloner: Cloner | None = None,
    ):
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
        self._db = sqlite3.connect(str(settings.data_dir / "workspace.db"), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(repos)")}
        if "requested_by" not in columns:  # registries created by an older version
            self._db.execute("ALTER TABLE repos ADD COLUMN requested_by TEXT NOT NULL DEFAULT ''")
        self._stores: OrderedDict[str, IndexStore] = OrderedDict()
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

    # ------------------------------------------------------------------ registry

    def _row(self, key: str) -> sqlite3.Row | None:
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
                "ORDER BY MAX(last_used, updated_at) DESC LIMIT ?",
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

    def _public(self, row: sqlite3.Row) -> dict:
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
            return self._db.execute(sql, args).fetchone()[0]

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
        return self.get(repo)

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
        tmp_index = self.index_dir / f"{key}.building.db"
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
            self._build_index(key, work_dir, tmp_index)
            self._install(key, work_dir, tmp_index)
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
            for suffix in ("", "-wal", "-shm"):
                Path(f"{tmp_index}{suffix}").unlink(missing_ok=True)
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

    def _build_index(self, key: str, root: Path, tmp_index: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{tmp_index}{suffix}").unlink(missing_ok=True)
        store = IndexStore(tmp_index)
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
        finally:
            store.conn.close()

    def _index_path(self, key: str) -> Path | None:
        row = self._row(key)
        if row is None or not row["index_file"]:
            return None
        return self.index_dir / row["index_file"]

    def _install(self, key: str, work_dir: Path, tmp_index: Path) -> None:
        """Swap the new index in under a new file name.

        Requests still reading the previous index keep using their open file; it is
        unlinked here and disappears once they finish. The checkout isn't needed after
        indexing (chunks are stored in the index), so it is deleted to save disk."""
        new_name = f"{key}.{time.time_ns()}.db"
        old_path = self._index_path(key)
        with self._lock:
            os.replace(tmp_index, self.index_dir / new_name)
            self._update(key, index_file=new_name)
            self._stores.pop(key, None)
            if old_path is not None:
                for suffix in ("", "-wal", "-shm"):
                    Path(f"{old_path}{suffix}").unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)

    def store(self, repo: RepoId) -> IndexStore | None:
        with self._lock:
            store = self._stores.get(repo.key)
            if store is not None:
                self._stores.move_to_end(repo.key)
                return store
            path = self._index_path(repo.key)
            if path is None or not path.exists():
                return None
            store = IndexStore(path)
            self._stores[repo.key] = store
            while len(self._stores) > MAX_OPEN_STORES:
                # Drop the least recently used. Its connection closes once no request uses it.
                self._stores.popitem(last=False)
            return store

    def touch(self, repo: RepoId) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE repos SET last_used = ? WHERE key = ?", (time.time(), repo.key))

    def _evict(self) -> None:
        """Keep at most ``max_repos`` indexed repositories; drop the least recently used."""
        with self._lock:
            rows = self._db.execute(
                "SELECT key FROM repos WHERE has_index = 1 AND status = 'ready' "
                "ORDER BY MAX(last_used, indexed_at) DESC"
            ).fetchall()
            for row in rows[self.settings.max_repos :]:
                self._delete(row["key"])

    def _delete(self, key: str) -> None:
        with self._lock:
            self._stores.pop(key, None)
            index = self._index_path(key)
            if index is not None:
                for suffix in ("", "-wal", "-shm"):
                    Path(f"{index}{suffix}").unlink(missing_ok=True)
            with self._db:
                self._db.execute("DELETE FROM repos WHERE key = ?", (key,))
            log.info("Evicted %s", key)
