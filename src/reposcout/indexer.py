"""Walk a repository and keep the index in sync with it (only changed files are re-embedded)."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

from .chunking import CHUNKER_VERSION, chunk_file
from .embeddings import Embedder
from .errors import ProviderError
from .store import IndexStore

log = logging.getLogger(__name__)

DEFAULT_IGNORES = [
    ".git/",
    ".hg/",
    ".svn/",
    ".reposcout/",
    "*.egg-info/",
    "node_modules/",
    "vendor/",
    "venv/",
    ".venv/",
    "env/",
    "__pycache__/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tox/",
    "dist/",
    "build/",
    "target/",
    ".next/",
    "coverage/",
    ".idea/",
    ".vscode/",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "*.pyc",
    "*.so",
    "*.dll",
    "*.exe",
    "*.bin",
    "*.dylib",
    "*.class",
    "*.jar",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.webp",
    "*.pdf",
    "*.zip",
    "*.gz",
    "*.tar",
    "*.mp3",
    "*.mp4",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.db",
    "*.sqlite",
    "*.ipynb",
]


@dataclass
class IndexStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_unchanged: int = 0
    files_removed: int = 0
    files_skipped: int = 0
    chunks_added: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.files_seen} files scanned: {self.files_indexed} indexed, "
            f"{self.files_unchanged} unchanged, {self.files_removed} removed, "
            f"{self.files_skipped} skipped; {self.chunks_added} chunks added in {self.seconds:.1f}s"
        )


def _spec(lines: list[str]) -> pathspec.PathSpec:
    try:
        return pathspec.PathSpec.from_lines("gitignore", lines)  # pathspec >= 1.0
    except (KeyError, LookupError):
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _git_files(root: Path) -> list[str] | None:
    """Ask git for tracked + untracked-but-not-ignored files (respects every .gitignore)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    patterns: list[str] = []
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        patterns = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    spec = _spec(patterns)
    out = []
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if not spec.match_file(rel):
                out.append(rel)
    return out


def iter_repo_files(root: Path, max_bytes: int) -> Iterator[tuple[str, Path]]:
    """Yield (repo-relative posix path, absolute path) for every file worth indexing."""
    rel_paths = _git_files(root)
    if rel_paths is None:
        rel_paths = _walk_files(root)

    extra: list[str] = []
    ignore_file = root / ".reposcoutignore"
    if ignore_file.is_file():
        extra = ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
    spec = _spec(DEFAULT_IGNORES + extra)

    for rel in sorted(set(rel_paths)):
        if spec.match_file(rel):
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        yield rel, path


def _read_text(path: Path) -> tuple[str, str] | None:
    """Return (text, sha256) or None for binary/unreadable files."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace"), hashlib.sha256(data).hexdigest()


class Indexer:
    def __init__(
        self, store: IndexStore, embedder: Embedder, max_file_bytes: int = 400_000, chunk_max_lines: int = 80
    ):
        self.store = store
        self.embedder = embedder
        self.max_file_bytes = max_file_bytes
        self.chunk_max_lines = chunk_max_lines

    def index(
        self, root: Path, rebuild: bool = False, progress: Callable[[str], None] | None = None
    ) -> IndexStats:
        started = time.perf_counter()
        root = root.resolve()
        if rebuild:
            self.store.clear()
        self.store.check_embedder(self.embedder.name)

        stats = IndexStats()
        known = self.store.file_hashes()
        if self.store.get_meta("chunker_version") != CHUNKER_VERSION:
            known = {path: "" for path in known}  # chunking changed: re-chunk every file
        seen: set[str] = set()

        for rel, path in iter_repo_files(root, self.max_file_bytes):
            stats.files_seen += 1
            read = _read_text(path)
            if read is None:
                stats.files_skipped += 1
                continue
            text, digest = read
            seen.add(rel)
            if known.get(rel) == digest:
                stats.files_unchanged += 1
                continue
            chunks = chunk_file(rel, text, max_lines=self.chunk_max_lines)
            if not chunks:
                # Remember the hash anyway so empty files aren't re-read every run.
                self.store.replace_file(rel, digest, [], [])
                stats.files_skipped += 1
                continue
            try:
                vectors = self.embedder.embed([c.embedding_text() for c in chunks])
            except ProviderError:
                raise  # provider is down: stop instead of failing every file
            except Exception as exc:  # one bad file should not stop the whole run
                stats.errors.append(f"{rel}: {exc}")
                continue
            self.store.replace_file(rel, digest, chunks, vectors)
            stats.files_indexed += 1
            stats.chunks_added += len(chunks)
            if progress:
                progress(f"indexed {rel} ({len(chunks)} chunks)")

        for rel in set(known) - seen:
            self.store.remove_file(rel)
            stats.files_removed += 1

        stats.seconds = time.perf_counter() - started
        self.store.set_meta("chunker_version", CHUNKER_VERSION)
        self.store.set_meta("last_indexed", time.strftime("%Y-%m-%dT%H:%M:%S"))
        return stats
