from reposcout.review.diff import parse_unified_diff

MULTI_FILE = """diff --git a/app/core.py b/app/core.py
index 1111111..2222222 100644
--- a/app/core.py
+++ b/app/core.py
@@ -10,5 +10,6 @@ def handler(event):
     data = load(event)
-    value = data["x"]
+    value = data.get("x")
+    -- not a header, just code starting with dashes
     return value

 def other():
@@ -40,2 +41,2 @@ class Thing:
-    a = 1
+    a = 2
     b = 2
diff --git a/new.py b/new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+print("hi")
+x = 1
\\ No newline at end of file
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,1 +0,0 @@
-old = True
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
--- a/old_name.py
+++ b/new_name.py
@@ -1 +1 @@
-a = 1
+a = 3
diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
"""


def test_parses_files_and_flags():
    files = {f.path: f for f in parse_unified_diff(MULTI_FILE)}
    assert set(files) == {"app/core.py", "new.py", "gone.py", "new_name.py", "logo.png"}
    assert files["new.py"].is_new
    assert files["gone.py"].is_deleted
    assert files["new_name.py"].is_rename and files["new_name.py"].old_path == "old_name.py"
    assert files["logo.png"].is_binary


def test_line_numbers_follow_the_new_file():
    core = parse_unified_diff(MULTI_FILE)[0]
    assert len(core.hunks) == 2
    added = core.added_lines()
    assert added[0] == (11, '    value = data.get("x")')
    assert added[1] == (12, "    -- not a header, just code starting with dashes")
    assert added[2] == (41, "    a = 2")
    # Context lines are commentable, removed lines are not.
    assert {10, 11, 12, 13} <= core.commentable_lines()
    assert core.additions == 3 and core.deletions == 2


def test_new_file_lines_and_no_newline_marker():
    new = next(f for f in parse_unified_diff(MULTI_FILE) if f.path == "new.py")
    assert new.added_lines() == [(1, 'print("hi")'), (2, "x = 1")]


def test_annotated_output_shows_new_line_numbers():
    core = parse_unified_diff(MULTI_FILE)[0]
    text = core.annotated()
    assert "   11 + " in text and "      - " in text


def test_plain_diff_without_git_header():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n a = 1\n-b = 2\n+b = 3\n"
    (file,) = parse_unified_diff(diff)
    assert file.path == "x.py" and file.added_lines() == [(2, "b = 3")]


def test_hunk_with_default_lengths_and_empty_input():
    diff = "--- a/y.py\n+++ b/y.py\n@@ -5 +5 @@\n-old\n+new\n"
    (file,) = parse_unified_diff(diff)
    assert file.added_lines() == [(5, "new")]
    assert parse_unified_diff("") == []
