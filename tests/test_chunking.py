from reposcout.chunking import chunk_file, detect_language


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


def test_javascript_definitions_detected():
    source = """import x from "y";

export function add(a, b) {
  return a + b;
}

export const mul = async (a, b) => {
  return a * b;
};

class Shape {
  area() { return 0; }
}
"""
    found = names(chunk_file("math.js", source))
    assert ("definition", "add") in found
    assert ("definition", "mul") in found
    assert ("definition", "Shape") in found


def test_go_and_markdown():
    go = 'package main\n\nimport "fmt"\n\nfunc (s *Server) Start(port int) error {\n\treturn nil\n}\n\ntype Server struct {\n\tName string\n}\n'
    assert ("definition", "Start") in names(chunk_file("main.go", go))
    md = "# Title\n\nIntro text.\n\n## Install\n\nRun pip.\nThen run it.\n"
    sections = chunk_file("README.md", md)
    assert [c.name for c in sections] == ["Title", "Install"]


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
