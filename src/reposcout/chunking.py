"""Split source files into chunks that follow the code's own structure.

Python files are parsed with the standard-library ``ast`` module, so every chunk is a
whole function, method or class. Other languages use lightweight regex detection of
definition lines (function, class, struct...). Anything else falls back to fixed-size
windows with a small overlap.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Bump when chunking logic changes, so existing indexes are rebuilt with the new chunks.
CHUNKER_VERSION = "2"

EXTENSION_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".cs": "csharp",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".rst": "text",
    ".txt": "text",
    ".toml": "config",
    ".yaml": "config",
    ".yml": "config",
    ".json": "config",
    ".ini": "config",
    ".cfg": "config",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".vue": "javascript",
    ".svelte": "javascript",
}

SPECIAL_FILENAMES = {"dockerfile": "config", "makefile": "config"}

_NAME = r"(?P<name>[A-Za-z_$][\w$]*)"
_C_LIKE_TYPES = rf"^\s*(?:(?:public|private|protected|internal|static|final|abstract|sealed|open|data|export|default)\s+)*(?:class|interface|enum|record|struct|object|trait)\s+{_NAME}"

DEFINITION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "javascript": [
        re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*{_NAME}"),
        re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+{_NAME}"),
        re.compile(
            rf"^\s*(?:export\s+)?(?:const|let|var)\s+{_NAME}\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>"
        ),
    ],
    "go": [
        re.compile(rf"^func\s+(?:\([^)]*\)\s*)?{_NAME}"),
        re.compile(rf"^type\s+{_NAME}\s+(?:struct|interface)"),
    ],
    "rust": [
        re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+{_NAME}"),
        re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+{_NAME}"),
        re.compile(rf"^\s*impl(?:<[^>]*>)?\s+(?:[\w:]+\s+for\s+)?{_NAME}"),
    ],
    "ruby": [
        re.compile(r"^\s*def\s+(?:self\.)?(?P<name>[\w?!=]+)"),
        re.compile(rf"^\s*(?:class|module)\s+{_NAME}"),
    ],
    "php": [
        re.compile(rf"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+{_NAME}"),
        re.compile(rf"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+{_NAME}"),
    ],
    "java": [
        re.compile(_C_LIKE_TYPES),
        re.compile(
            rf"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|override|suspend)\s+)+"
            rf"[\w<>\[\], ?]+\s+{_NAME}\s*\("
        ),
    ],
    "kotlin": [
        re.compile(_C_LIKE_TYPES),
        re.compile(
            rf"^\s*(?:(?:public|private|protected|internal|override|suspend|inline)\s+)*fun\s+(?:<[^>]*>\s*)?{_NAME}"
        ),
    ],
    "shell": [re.compile(rf"^\s*(?:function\s+)?{_NAME}\s*\(\)\s*\{{")],
    "markdown": [re.compile(r"^#{1,4}\s+(?P<name>.+?)\s*#*\s*$")],
    "sql": [
        re.compile(
            r"^\s*create\s+(?:or\s+replace\s+)?(?:table|view|function|procedure|index)\s+(?:if\s+not\s+exists\s+)?(?P<name>[\w.\"]+)",
            re.I,
        )
    ],
}
DEFINITION_PATTERNS["typescript"] = DEFINITION_PATTERNS["javascript"] + [
    re.compile(rf"^\s*(?:export\s+)?(?:interface|type|enum)\s+{_NAME}"),
]
DEFINITION_PATTERNS["csharp"] = DEFINITION_PATTERNS["java"]
DEFINITION_PATTERNS["scala"] = DEFINITION_PATTERNS["kotlin"]
DEFINITION_PATTERNS["swift"] = DEFINITION_PATTERNS["kotlin"]


@dataclass(frozen=True)
class Chunk:
    path: str  # repo-relative, forward slashes
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    kind: str  # function | method | class | module | section | block
    name: str | None
    language: str
    text: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def embedding_text(self) -> str:
        """Text sent to the embedding model: a short header helps retrieval a lot."""
        header = f"File: {self.path}"
        if self.name:
            header += f"\n{self.kind.capitalize()}: {self.name}"
        return f"{header}\n\n{self.text}"


def detect_language(path: str) -> str:
    p = PurePosixPath(path)
    special = SPECIAL_FILENAMES.get(p.name.lower())
    if special:
        return special
    return EXTENSION_LANGUAGES.get(p.suffix.lower(), "text")


def chunk_file(path: str, source: str, max_lines: int = 80, overlap: int = 10) -> list[Chunk]:
    """Split one file into chunks. ``path`` is only used for labels and language detection."""
    if not source.strip():
        return []
    language = detect_language(path)
    lines = source.splitlines()
    if language == "python":
        chunks = _chunk_python(path, source, lines, max_lines, overlap)
        if chunks is not None:
            return chunks
    patterns = DEFINITION_PATTERNS.get(language)
    if patterns:
        return _chunk_by_patterns(path, lines, language, patterns, max_lines, overlap)
    return _windows(path, lines, 1, len(lines), language, "block", None, max_lines, overlap)


# --------------------------------------------------------------------------- python


def _chunk_python(path, source, lines, max_lines, overlap) -> list[Chunk] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None  # caller falls back to the generic chunker

    chunks: list[Chunk] = []
    covered: set[int] = set()
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def start_of(node: ast.AST) -> int:
        decorators = getattr(node, "decorator_list", [])
        return min([node.lineno] + [d.lineno for d in decorators])

    def emit(node: ast.AST, qualname: str, kind: str) -> None:
        """Emit a definition as one chunk; if it is too long, split it into its nested
        definitions (methods, inner functions) plus the code around them."""
        start, end = start_of(node), node.end_lineno or node.lineno
        children = [n for n in node.body if isinstance(n, definitions)]
        if end - start + 1 <= max_lines or not children:
            covered.update(range(start, end + 1))
            chunks.extend(_windows(path, lines, start, end, "python", kind, qualname, max_lines, overlap))
            return
        is_class = isinstance(node, ast.ClassDef)
        for child in children:
            child_kind = (
                "class" if isinstance(child, ast.ClassDef) else ("method" if is_class else "function")
            )
            emit(child, f"{qualname}.{child.name}", child_kind)
        # The rest of the body (signature, docstring, attributes, glue code) stays with the parent name.
        chunks.extend(
            _leftover_chunks(path, lines, covered, "python", max_lines, overlap, start, end, kind, qualname)
        )
        covered.update(range(start, end + 1))

    for node in tree.body:
        if isinstance(node, definitions):
            emit(node, node.name, "class" if isinstance(node, ast.ClassDef) else "function")

    # Whatever is left (imports, constants, script code) becomes "module" chunks.
    chunks.extend(_leftover_chunks(path, lines, covered, "python", max_lines, overlap))
    chunks.sort(key=lambda c: (c.start_line, c.end_line))
    return chunks


def _leftover_chunks(
    path,
    lines,
    covered,
    language,
    max_lines,
    overlap,
    first: int = 1,
    last: int | None = None,
    kind: str = "module",
    name: str | None = None,
) -> list[Chunk]:
    """Chunk every run of lines in [first, last] that no definition has claimed."""
    last = len(lines) if last is None else last
    chunks: list[Chunk] = []
    run_start = None
    for lineno in range(first, last + 2):
        free = lineno <= last and lineno not in covered
        if free and run_start is None:
            run_start = lineno
        elif not free and run_start is not None:
            run_end = lineno - 1
            if any(lines[n - 1].strip() for n in range(run_start, run_end + 1)):
                chunks.extend(
                    _windows(path, lines, run_start, run_end, language, kind, name, max_lines, overlap)
                )
            run_start = None
    return chunks


# --------------------------------------------------------------------------- other languages


def _chunk_by_patterns(path, lines, language, patterns, max_lines, overlap) -> list[Chunk]:
    boundaries: list[tuple[int, str]] = []  # (1-based line, name)
    for i, line in enumerate(lines, start=1):
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                boundaries.append((i, match.group("name").strip()))
                break

    kind = "section" if language == "markdown" else "definition"
    segments: list[tuple[int, int, str | None]] = []
    if not boundaries or boundaries[0][0] > 1:
        first = boundaries[0][0] - 1 if boundaries else len(lines)
        segments.append((1, first, None))
    for idx, (start, name) in enumerate(boundaries):
        end = boundaries[idx + 1][0] - 1 if idx + 1 < len(boundaries) else len(lines)
        segments.append((start, end, name))

    # Merge tiny segments (one-liners, blank gaps) into the previous one.
    merged: list[tuple[int, int, str | None]] = []
    for start, end, name in segments:
        if merged and end - start + 1 < 3:
            p_start, _, p_name = merged[-1]
            merged[-1] = (p_start, end, p_name)
        else:
            merged.append((start, end, name))

    chunks: list[Chunk] = []
    for start, end, name in merged:
        if not any(lines[n - 1].strip() for n in range(start, end + 1)):
            continue
        seg_kind = kind if name else "module"
        chunks.extend(_windows(path, lines, start, end, language, seg_kind, name, max_lines, overlap))
    return chunks


# --------------------------------------------------------------------------- windows


def _windows(path, lines, start, end, language, kind, name, max_lines, overlap) -> list[Chunk]:
    """Emit lines[start..end] as one chunk, or several overlapping ones if it is too long."""
    out: list[Chunk] = []
    step = max(1, max_lines - overlap)
    window_start = start
    while window_start <= end:
        window_end = min(end, window_start + max_lines - 1)
        # Trim blank lines at the edges so line ranges point at real code.
        a, b = window_start, window_end
        while a < b and not lines[a - 1].strip():
            a += 1
        while b > a and not lines[b - 1].strip():
            b -= 1
        text = "\n".join(lines[a - 1 : b])
        if text.strip():
            out.append(Chunk(path, a, b, kind, name, language, text))
        if window_end >= end:
            break
        window_start += step
    return out
