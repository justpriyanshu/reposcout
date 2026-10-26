import pytest

from reposcout import treesitter
from reposcout.chunking import chunk_file, detect_language

needs_grammars = pytest.mark.skipif(
    treesitter._load("javascript")[0] is None or treesitter._load("go")[0] is None,
    reason="tree-sitter grammars could not be downloaded",
)


@pytest.fixture
def regex_only(monkeypatch):
    monkeypatch.setenv("REPOSCOUT_TREE_SITTER", "off")


def names(chunks):
    return [(c.kind, c.name) for c in chunks]


def test_python_functions_and_classes_become_chunks():
    source = """import os

CONSTANT = 1


def top(a, b):
    return a + b


@decorator
def decorated():
    pass


class Small:
    x = 1

    def method(self):
        return self.x
"""
    chunks = chunk_file("pkg/mod.py", source)
    assert ("function", "top") in names(chunks)
    assert ("class", "Small") in names(chunks)
    assert ("module", None) in names(chunks)
    decorated = next(c for c in chunks if c.name == "decorated")
    assert decorated.text.startswith("@decorator")  # decorator lines belong to the function
    assert decorated.start_line == 10


def test_large_class_is_split_into_methods_with_header():
    methods = "\n".join(
        f"    def m{i}(self):\n" + "\n".join(f"        x = {j}" for j in range(8)) + "\n" for i in range(12)
    )
    source = f'class Big:\n    """Docs."""\n    attr = 1\n\n{methods}'
    chunks = chunk_file("big.py", source, max_lines=40)
    found = names(chunks)
    assert ("method", "Big.m0") in found and ("method", "Big.m11") in found
    header = next(c for c in chunks if c.kind == "class")
    assert header.start_line == 1 and "Docs." in header.text
    # No line of the class is lost or double-claimed as "module" code.
    assert not any(c.kind == "module" for c in chunks)


def test_long_function_with_nested_functions_splits_by_inner_definitions():
    inner = "\n".join(
        f"    def handler_{i}():\n" + "\n".join(f"        y = {j}" for j in range(10)) + "\n"
        for i in range(5)
    )
    source = f"def create_app():\n    app = object()\n\n{inner}\n    return app\n"
    chunks = chunk_file("app.py", source, max_lines=30)
    assert ("function", "create_app.handler_0") in names(chunks)
    assert ("function", "create_app") in names(chunks)


def test_long_function_without_children_uses_overlapping_windows():
    body = "\n".join(f"    v{i} = {i}" for i in range(100))
    chunks = chunk_file("long.py", f"def long():\n{body}\n", max_lines=40, overlap=10)
    assert len(chunks) >= 3
    assert all(c.name == "long" for c in chunks)
    assert chunks[0].end_line > chunks[1].start_line  # windows overlap
    assert chunks[-1].end_line == 101


def test_syntax_error_falls_back_to_windows():
    chunks = chunk_file("broken.py", "def broken(:\n    pass\n")
    assert len(chunks) == 1 and chunks[0].kind == "block"


JS_SOURCE = """import x from "y";

/** Adds two numbers. */
export function add(a, b) {
  return sum(a, b);
}

export const mul = async (a, b) => {
  return a * helper(b);
};

class Shape {
  area() { return compute(0); }
}
"""

GO_SOURCE = 'package main\n\nimport "fmt"\n\nfunc (s *Server) Start(port int) error {\n\tfmt.Println(port)\n\treturn s.listen(port)\n}\n\ntype Server struct {\n\tName string\n}\n'


@needs_grammars
def test_javascript_definitions_parsed_with_tree_sitter():
    chunks = chunk_file("math.js", JS_SOURCE)
    found = names(chunks)
    assert ("function", "add") in found
    assert ("function", "mul") in found
    assert ("class", "Shape") in found
    add = next(c for c in chunks if c.name == "add")
    assert add.text.startswith("/** Adds")  # doc comment and `export` belong to the function
    assert add.refs == ("sum",)
    mul = next(c for c in chunks if c.name == "mul")
    assert mul.text.startswith("export const mul")


@needs_grammars
def test_go_methods_and_types_with_call_refs():
    chunks = chunk_file("main.go", GO_SOURCE)
    assert ("method", "Start") in names(chunks)
    assert ("class", "Server") in names(chunks)
    start = next(c for c in chunks if c.name == "Start")
    assert start.refs == ("Println", "listen")  # calls only, not type names


@needs_grammars
def test_large_typescript_class_splits_into_qualified_methods():
    body = "".join(f"  m{i}() {{\n" + "".join(f"    x{j}();\n" for j in range(8)) + "  }\n" for i in range(8))
    chunks = chunk_file("big.ts", f"class Big {{\n{body}}}\n", max_lines=30)
    assert ("method", "Big.m0") in names(chunks) and ("method", "Big.m7") in names(chunks)
    assert next(c for c in chunks if c.name == "Big.m3").refs[:2] == ("x0", "x1")


@needs_grammars
def test_rust_impl_blocks_hold_their_methods():
    source = (
        "#[derive(Debug)]\npub struct A { x: i32 }\n\nimpl A {\n"
        + "".join(
            f"    pub fn f{i}(&self) -> i32 {{\n"
            + "".join(f"        g{j}();\n" for j in range(6))
            + "        0\n    }\n"
            for i in range(6)
        )
        + "}\n"
    )
    chunks = chunk_file("a.rs", source, max_lines=20)
    struct = chunks[0]
    assert struct.name == "A" and struct.text.startswith("#[derive(Debug)]")
    assert ("method", "A.f0") in names(chunks)


def test_regex_fallback_without_tree_sitter(regex_only):
    found = names(chunk_file("math.js", JS_SOURCE))
    assert ("definition", "add") in found
    assert ("definition", "mul") in found
    assert ("definition", "Shape") in found
    assert ("definition", "Start") in names(chunk_file("main.go", GO_SOURCE))
    add = next(c for c in chunk_file("math.js", JS_SOURCE) if c.name == "add")
    assert "sum" in add.refs and "function" not in add.refs


def test_python_chunks_record_calls():
    (chunk,) = chunk_file("a.py", "def a():\n    return b(1) + self.c() + a()\n")
    assert chunk.refs == ("b", "c")  # recursion into itself is not a reference


def test_markdown_sections():
    md = "# Title\n\nIntro text.\n\n## Install\n\nRun pip.\nThen run it.\n"
    sections = chunk_file("README.md", md)
    assert [c.name for c in sections] == ["Title", "Install"]
    assert all(c.refs == () for c in sections)


def test_empty_and_blank_files_produce_no_chunks():
    assert chunk_file("a.py", "") == []
    assert chunk_file("a.txt", "\n\n   \n") == []


def test_line_ranges_skip_blank_edges_and_text_matches():
    source = "\n\n\ndef f():\n    return 1\n\n\n"
    (chunk,) = chunk_file("f.py", source)
    assert (chunk.start_line, chunk.end_line) == (4, 5)
    assert chunk.text == "def f():\n    return 1"


def test_language_detection():
    assert detect_language("src/a.tsx") == "typescript"
    assert detect_language("Dockerfile") == "config"
    assert detect_language("notes.unknown") == "text"
