"""Parse unified diffs (``git diff`` / GitHub ``.diff`` format) into files, hunks and lines.

Line numbers matter: GitHub only accepts inline review comments on lines that appear in
the diff, so every added/context line keeps its line number in the new file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class DiffLine:
    kind: str  # "+" added, "-" removed, " " context
    content: str
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)
    is_binary: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def is_new(self) -> bool:
        return self.old_path is None

    @property
    def is_deleted(self) -> bool:
        return self.new_path is None

    @property
    def is_rename(self) -> bool:
        return bool(self.old_path and self.new_path and self.old_path != self.new_path)

    def added_lines(self) -> list[tuple[int, str]]:
        return [
            (line.new_lineno, line.content)
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind == "+" and line.new_lineno is not None
        ]

    def added_line_numbers(self) -> set[int]:
        return {n for n, _ in self.added_lines()}

    def commentable_lines(self) -> set[int]:
        """New-file lines GitHub accepts comments on (added or context lines)."""
        return {
            line.new_lineno
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind in "+ " and line.new_lineno is not None
        }

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for line in h.lines if line.kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for line in h.lines if line.kind == "-")

    def annotated(self) -> str:
        """Render the diff with new-file line numbers, the format the reviewer prompt uses."""
        out: list[str] = []
        for hunk in self.hunks:
            out.append(f"@@ {hunk.header.strip()}".rstrip() if hunk.header.strip() else "@@")
            for line in hunk.lines:
                number = f"{line.new_lineno:>5}" if line.new_lineno is not None else "     "
                out.append(f"{number} {line.kind} {line.content}")
        return "\n".join(out)


def _strip_prefix(path: str) -> str | None:
    path = path.strip().split("\t")[0]
    if path == "/dev/null":
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path[:2] in ("a/", "b/"):
        return path[2:]
    return path


def parse_unified_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    old_left = new_left = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            match = re.match(r"diff --git (\S+) (\S+)", raw)
            current = FileDiff(
                old_path=_strip_prefix(match.group(1)) if match else None,
                new_path=_strip_prefix(match.group(2)) if match else None,
            )
            files.append(current)
            hunk = None
            continue

        in_hunk = hunk is not None and (old_left > 0 or new_left > 0)

        if not in_hunk and raw.startswith("--- "):
            if current is None or current.hunks:
                current = FileDiff(old_path=None, new_path=None)
                files.append(current)
            current.old_path = _strip_prefix(raw[4:])
            hunk = None
            continue
        if not in_hunk and raw.startswith("+++ ") and current is not None:
            current.new_path = _strip_prefix(raw[4:])
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.old_path = None
            continue
        if raw.startswith("deleted file mode"):
            current.new_path = None
            continue
        if raw.startswith("rename from "):
            current.old_path = raw[len("rename from ") :]
            continue
        if raw.startswith("rename to "):
            current.new_path = raw[len("rename to ") :]
            continue
        if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current.is_binary = True
            continue

        match = HUNK_RE.match(raw)
        if match:  # a hunk body line never starts with "@", so this is always a new hunk
            old_start, old_len, new_start, new_len, header = match.groups()
            hunk = Hunk(
                int(old_start),
                int(old_len) if old_len is not None else 1,
                int(new_start),
                int(new_len) if new_len is not None else 1,
                header or "",
            )
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
            old_left, new_left = hunk.old_len, hunk.new_len
            continue

        if hunk is None or not in_hunk:
            continue
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        kind, content = (raw[0], raw[1:]) if raw else (" ", "")
        if kind == "+":
            hunk.lines.append(DiffLine("+", content, None, new_no))
            new_no += 1
            new_left -= 1
        elif kind == "-":
            hunk.lines.append(DiffLine("-", content, old_no, None))
            old_no += 1
            old_left -= 1
        else:
            hunk.lines.append(DiffLine(" ", content, old_no, new_no))
            old_no += 1
            new_no += 1
            old_left -= 1
            new_left -= 1

    return [f for f in files if f.path]
