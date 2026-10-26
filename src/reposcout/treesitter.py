"""Structure-aware chunking for non-Python languages with tree-sitter.

Grammars come from ``tree-sitter-language-pack``, which downloads each grammar (a small
shared library) on first use and caches it. Each language's *tags query* (the same one
GitHub uses for code navigation) marks definitions (``@definition.function``...) and
references (``@reference.call``...), which gives both chunk boundaries and a symbol graph:
every chunk records the names it calls, so "who calls X?" is a lookup.

Anything that goes wrong here (no grammar, no network for the first download, a parser
error) returns ``None`` and the caller falls back to the regex chunker.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

# Our language label -> grammar name in the language pack.
GRAMMARS = {
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "java": "java",
    "kotlin": "kotlin",
    "scala": "scala",
    "csharp": "csharp",
    "swift": "swift",
    "c": "c",
    "cpp": "cpp",
}
# The TypeScript grammars extend JavaScript, but their tags queries only add the TS-only
# constructs, so both queries are combined.
EXTRA_QUERIES = {"typescript": ["javascript"], "tsx": ["javascript"]}

DEFINITION_KINDS = {
    "class": "class",
    "interface": "class",
    "module": "class",
    "type": "class",
    "function": "function",
    "method": "method",
    "macro": "function",
}
CALL_TAGS = {"call", "send"}  # type references would flood refs with int, string...
CONTAINERS = {"class"}  # definitions inside these get a qualified name (Class.method)

# Nodes that wrap a definition without adding meaning: `export`, `const x = () => ...`,
# decorators. The chunk starts at the wrapper so it includes `export`, `const`, `@decorator`.
WRAPPERS = {
    "export_statement",
    "lexical_declaration",
    "variable_declaration",
    "decorated_definition",
    "template_declaration",
    "ambient_declaration",
}
# Siblings directly above a definition that belong to it (doc comments, attributes).
LEADING = ("comment", "attribute_item", "attribute", "annotation", "decorator")
CALL_TYPES = (
    "call_expression",
    "call",
    "method_invocation",
    "invocation_expression",
    "function_call_expression",
    "member_call_expression",
    "method_call_expression",
    "scoped_call_expression",
)
CALLEE_FIELDS = ("function", "name", "method")
MAX_REFS_PER_CHUNK = 64

_lock = threading.Lock()
_parsers: dict[str, Any] = {}
_queries: dict[str, Any] = {}
_configured = False


def enabled() -> bool:
    return os.environ.get("REPOSCOUT_TREE_SITTER", "auto").strip().lower() not in ("0", "off", "false", "no")


def _configure(pack) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    cache = os.environ.get("REPOSCOUT_GRAMMAR_DIR")
    if cache:
        pack.configure(pack.PackConfig(cache_dir=cache))


def grammar_for(path: str, language: str) -> str | None:
    if language == "typescript" and PurePosixPath(path).suffix.lower() == ".tsx":
        return "tsx"
    return GRAMMARS.get(language)


def _load(grammar: str):
    """(parser, query) for a grammar, or (None, None) if it can't be loaded. Cached."""
    with _lock:
        if grammar in _parsers:
            return _parsers[grammar], _queries[grammar]
        parser = query = None
        try:
            import tree_sitter as ts
            import tree_sitter_language_pack as pack

            _configure(pack)
            parser = pack.get_parser(grammar)
            language = pack.get_language(grammar)
            sources = [pack.get_tags_query(g) or "" for g in [*EXTRA_QUERIES.get(grammar, []), grammar]]
            try:
                query = ts.Query(language, "\n".join(sources))
            except Exception:  # an extra query may not compile against this grammar
                query = ts.Query(language, sources[-1]) if sources[-1] else None
        except Exception as exc:  # no package, no network for the first download, ...
            log.info("tree-sitter grammar %s unavailable (%s); using the regex chunker", grammar, exc)
            parser = query = None
        _parsers[grammar], _queries[grammar] = parser, query
        return parser, query


def prefetch(grammars: list[str] | None = None) -> list[str]:
    """Download grammars ahead of time (for Docker images and offline use). Returns the
    grammars that are ready."""
    names = sorted(set(grammars or [*GRAMMARS.values(), "tsx"]))
    try:
        import tree_sitter_language_pack as pack

        _configure(pack)
        pack.download(names)
    except Exception as exc:
        log.warning("could not download tree-sitter grammars: %s", exc)
    return [g for g in names if _load(g)[0] is not None]


# Signature of chunking._windows, passed in to avoid a circular import.
Windows = Callable[..., list]


def chunk_tree_sitter(
    path: str,
    source: str,
    lines: list[str],
    language: str,
    max_lines: int,
    overlap: int,
    windows: Windows,
    leftovers: Callable[..., list],
) -> list | None:
    """Chunk one file along its definitions; ``None`` means "use the fallback chunker"."""
    if not enabled():
        return None
    grammar = grammar_for(path, language)
    if grammar is None:
        return None
    parser, query = _load(grammar)
    if parser is None or query is None:
        return None
    try:
        import tree_sitter as ts

        tree = parser.parse(source.encode("utf-8"))
        definitions, references = _tags(ts, query, tree.root_node)
        references.extend(_calls(tree.root_node))
    except Exception as exc:
        log.debug("tree-sitter failed on %s: %s", path, exc)
        return None
    if tree.root_node.has_error and not definitions:
        return None

    chunks: list = []
    covered: set[int] = set()
    nodes = _nest(definitions)

    def emit(item: dict, qualname: str) -> None:
        start, end, kind = item["start"], item["end"], item["kind"]
        children = item["children"]
        if end - start + 1 <= max_lines or not children:
            covered.update(range(start, end + 1))
            chunks.extend(windows(path, lines, start, end, language, kind, qualname, max_lines, overlap))
            return
        for child in children:
            child_name = f"{qualname}.{child['name']}" if kind in CONTAINERS else child["name"]
            emit(child, child_name)
        chunks.extend(
            leftovers(path, lines, covered, language, max_lines, overlap, start, end, kind, qualname)
        )
        covered.update(range(start, end + 1))

    for item in nodes:
        emit(item, item["name"])
    chunks.extend(leftovers(path, lines, covered, language, max_lines, overlap))
    chunks.sort(key=lambda c: (c.start_line, c.end_line))
    return attach_refs(chunks, references)


def _tags(ts, query, root) -> tuple[list[dict], list[tuple[int, str]]]:
    definitions: list[dict] = []
    references: list[tuple[int, str]] = []
    seen: set[tuple[int, int]] = set()
    for _pattern, captures in ts.QueryCursor(query).matches(root):
        names = captures.get("name") or []
        if not names:
            continue
        name = _text(names[0])
        for capture, nodes in captures.items():
            role, _, tag = capture.partition(".")
            if role == "reference" and nodes[0].type == "impl_item":
                role, tag = "definition", "class"  # Rust `impl Type { ... }` holds methods
            if role == "definition" and tag in DEFINITION_KINDS:
                node = nodes[0]
                key = (node.start_byte, node.end_byte)
                if key in seen or not name:
                    continue
                seen.add(key)
                start = _start_row(node)
                definitions.append(
                    {
                        "start": start + 1,
                        "end": node.end_point.row + 1,
                        "name": name,
                        "kind": DEFINITION_KINDS[tag],
                        "children": [],
                    }
                )
            elif role == "reference" and tag in CALL_TAGS and name:
                references.append((names[0].start_point.row + 1, name))
    return definitions, references


def _start_row(node) -> int:
    """First row of a definition, including its wrapper and leading comments/attributes."""
    while node.parent is not None and node.parent.type in WRAPPERS and node.parent.end_byte == node.end_byte:
        node = node.parent
    row = node.start_point.row
    prev = node.prev_sibling
    while prev is not None and prev.type.endswith(LEADING) and prev.end_point.row >= row - 1:
        row = prev.start_point.row
        prev = prev.prev_sibling
    return row


def _nest(definitions: list[dict]) -> list[dict]:
    """Turn a flat list into a tree by line containment; returns the top-level items."""
    definitions.sort(key=lambda d: (d["start"], -d["end"]))
    roots: list[dict] = []
    stack: list[dict] = []
    for item in definitions:
        while stack and not (stack[-1]["start"] <= item["start"] and item["end"] <= stack[-1]["end"]):
            stack.pop()
        if stack and (stack[-1]["start"], stack[-1]["end"]) == (item["start"], item["end"]):
            continue  # the same span captured twice (e.g. as class and as type)
        (stack[-1]["children"] if stack else roots).append(item)
        stack.append(item)
    return roots


def _calls(root) -> list[tuple[int, str]]:
    """Names of called functions anywhere in the tree. Tags queries miss some call shapes
    (and TypeScript's has none), so this complements them; duplicates are dropped later."""
    out: list[tuple[int, str]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in CALL_TYPES:
            name = _callee(node)
            if name:
                out.append((node.start_point.row + 1, name))
        stack.extend(node.children)
    return out


def _callee(node) -> str | None:
    target = None
    for field in CALLEE_FIELDS:
        target = node.child_by_field_name(field)
        if target is not None:
            break
    if target is None:
        return None
    # `a.b.c()` / `a::b()` / `a->b()`: the last identifier is the function's own name.
    while target.named_child_count:
        last = target.named_children[-1]
        if last.type in ("arguments", "argument_list", "type_arguments"):
            break
        target = last
    text = _text(target)
    return text if text.isidentifier() else None


def _text(node) -> str:
    return node.text.decode("utf-8", "replace") if node.text else ""


def attach_refs(chunks: list, references: list[tuple[int, str]]) -> list:
    """Give every chunk the distinct names referenced inside its line range."""
    if not references:
        return chunks
    from dataclasses import replace

    references.sort()
    out = []
    for chunk in chunks:
        own = (chunk.name or "").rsplit(".", 1)[-1]
        names = list(
            dict.fromkeys(
                n for line, n in references if chunk.start_line <= line <= chunk.end_line and n != own
            )
        )
        out.append(replace(chunk, refs=tuple(names[:MAX_REFS_PER_CHUNK])) if names else chunk)
    return out
