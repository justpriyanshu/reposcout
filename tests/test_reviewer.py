from reposcout.review import Reviewer, Severity
from reposcout.review.reviewer import extract_json

from .conftest import FakeLLM, findings_reply

DIFF = """diff --git a/billing/invoice.py b/billing/invoice.py
--- a/billing/invoice.py
+++ b/billing/invoice.py
@@ -6,4 +6,6 @@ TAX_RATE = 0.18
 def calculate_total(items, discount=0.0):
     \"\"\"Sum item prices, apply a discount and add tax.\"\"\"
     subtotal = sum(item.price * item.quantity for item in items)
-    return round((subtotal - discount) * (1 + TAX_RATE), 2)
+    total = (subtotal - discount) * (1 + TAX_RATE)
+    cur.execute(f"INSERT INTO totals VALUES ({total})")
+    return total
"""
# Added lines: 9 (total = ...), 10 (execute), 11 (return total)


def finding(line, **overrides):
    base = {
        "line": line,
        "severity": "major",
        "category": "bug",
        "title": "Rounding removed",
        "message": "The total is no longer rounded to two decimals.",
        "suggestion": "return round(total, 2)",
    }
    return {**base, **overrides}


def test_llm_and_rule_findings_are_combined():
    llm = FakeLLM(findings_reply([finding(11)]))
    result = Reviewer(llm).review(DIFF)
    sources = {(f.line, f.source) for f in result.findings}
    assert (11, "llm") in sources
    assert (10, "rule:sql-format") in sources
    assert result.file_summaries["billing/invoice.py"] == "Changes the code."
    assert result.stats.llm_calls == 1
    # The prompt shows numbered lines so the model can point at them.
    _, prompt, json_mode = llm.calls[0]
    assert json_mode and "   10 + " in prompt


def test_findings_on_lines_outside_the_diff_are_dropped_or_snapped():
    llm = FakeLLM(findings_reply([finding(500), finding(12, title="Near miss")]))
    result = Reviewer(llm).review(DIFF)
    llm_findings = [f for f in result.findings if f.source == "llm"]
    assert [f.line for f in llm_findings] == [11]  # 12 is 1 line away from added line 11 -> snapped
    assert result.stats.dropped_off_diff == 1
    assert result.stats.snapped_lines == 1


def test_invalid_findings_are_dropped_not_fatal():
    bad = [finding(9, severity="catastrophic"), {"line": "abc"}, "not an object", finding(9, title="")]
    result = Reviewer(FakeLLM(findings_reply(bad))).review(DIFF)
    assert all(f.source.startswith("rule:") for f in result.findings)
    assert result.stats.dropped_invalid == 4


def test_aliases_are_normalised():
    result = Reviewer(FakeLLM(findings_reply([finding(9, severity="HIGH", category="correctness")]))).review(
        DIFF
    )
    llm_finding = next(f for f in result.findings if f.source == "llm")
    assert llm_finding.severity == Severity.major and llm_finding.category.value == "bug"


def test_duplicate_of_rule_finding_is_merged():
    dup = finding(10, category="security", severity="major", title="SQL injection")
    result = Reviewer(FakeLLM(findings_reply([dup]))).review(DIFF)
    at_10 = [f for f in result.findings if f.line == 10]
    assert len(at_10) == 1 and at_10[0].source == "rule:sql-format"
    assert result.stats.duplicates_removed == 1


def test_llm_failure_keeps_rule_findings():
    def boom(system, user):
        raise RuntimeError("model crashed")

    result = Reviewer(FakeLLM(boom)).review(DIFF)
    assert [f.source for f in result.findings] == ["rule:sql-format"]
    assert result.stats.llm_errors == 1


def test_unparseable_reply_counts_as_invalid():
    result = Reviewer(FakeLLM("I think it's fine!")).review(DIFF)
    assert result.stats.dropped_invalid == 1


def test_min_severity_and_per_file_cap():
    many = [finding(9, severity="nit", category="style", title=f"Nit {i}") for i in range(3)]
    many += [finding(11, severity="minor", category="maintainability", title="Name it better")]
    reviewer = Reviewer(FakeLLM(findings_reply(many)), min_severity=Severity.minor, max_findings_per_file=1)
    result = reviewer.review(DIFF)
    assert len(result.findings) == 1
    assert result.findings[0].severity == Severity.major  # the highest severity survives the cap


def test_rules_only_mode_and_skips():
    diff = (
        DIFF
        + """diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-{}
+{"a": 1}
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""
    )
    result = Reviewer(None).review(diff)
    assert not result.llm_used
    assert result.files_reviewed == ["billing/invoice.py"]
    assert result.files_skipped == {"package-lock.json": "generated or vendored", "gone.py": "deleted"}
    assert result.findings[0].source == "rule:sql-format"


def test_related_context_from_the_index_is_added(make_assistant):
    diff = """diff --git a/api/routes.py b/api/routes.py
--- a/api/routes.py
+++ b/api/routes.py
@@ -1,2 +1,3 @@
 def checkout(cart):
-    return 0
+    return calculate_total(cart.items, discount=cart.coupon)
"""
    llm = FakeLLM(findings_reply([]))
    Reviewer(llm, make_assistant()).review(diff)
    prompt = llm.calls[0][1]
    assert "Related code" in prompt
    assert "billing/invoice.py" in prompt and "def calculate_total" in prompt


def test_markdown_and_text_rendering():
    result = Reviewer(FakeLLM(findings_reply([finding(11)]))).review(DIFF)
    md = result.to_markdown()
    assert "RepoScout review" in md and "`billing/invoice.py:11`" in md and "Suggested fix" in md
    text = result.to_text()
    assert "[MAJOR] billing/invoice.py:10" in text
    assert result.worst() == Severity.major
    assert result.to_dict()["counts"]["major"] == 2


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Here you go: {"a": 3} hope it helps') == {"a": 3}
    assert extract_json('[{"line": 1}]') == [{"line": 1}]
    assert extract_json("no json here") is None


def test_weaker_restatement_on_neighbouring_line_is_dropped():
    diff = """--- a/app/sync.py
+++ b/app/sync.py
@@ -1,2 +1,4 @@
 def sync(client):
     try:
         client.push()
+    except:
+        pass
"""
    llm = FakeLLM(
        findings_reply(
            [
                finding(4, severity="major", category="bug", title="Errors are swallowed"),
                finding(5, severity="minor", category="maintainability", title="Empty pass statement"),
            ]
        )
    )
    result = Reviewer(llm).review(diff)
    titles = [f.title for f in result.findings]
    assert "Empty pass statement" not in titles
    assert result.stats.duplicates_removed >= 1


def test_separate_problems_of_equal_severity_on_neighbouring_lines_are_kept():
    llm = FakeLLM(
        findings_reply(
            [
                finding(9, severity="major", category="bug", title="Rounding removed"),
                finding(10, severity="major", category="performance", title="Query per item"),
            ]
        )
    )
    result = Reviewer(llm).review(DIFF)
    titles = {f.title for f in result.findings}
    assert {"Rounding removed", "Query per item"} <= titles


def test_review_prompt_discourages_noise():
    from reposcout.review.reviewer import REVIEW_SYSTEM_PROMPT

    prompt = REVIEW_SYSTEM_PROMPT.format(max_findings=6)
    assert "Only report problems this change introduces" in prompt
    assert "One finding per root cause" in prompt
    assert "missing type hints" in prompt


def test_callers_of_a_changed_function_are_added(sample_repo, settings):
    from reposcout.assistant import RepoAssistant
    from reposcout.embeddings import HashingEmbedder
    from reposcout.indexer import Indexer
    from reposcout.store import IndexStore

    (sample_repo / "api").mkdir()
    (sample_repo / "api" / "login.py").write_text(
        "from auth.session import verify_token\n\n\n"
        "def login(request):\n"
        "    if not verify_token(request.secret, request.token, request.sig):\n"
        "        raise PermissionError\n"
        "    return True\n"
    )
    store = IndexStore(settings.index_path)
    embedder = HashingEmbedder()
    Indexer(store, embedder).index(sample_repo)
    diff = """diff --git a/auth/session.py b/auth/session.py
--- a/auth/session.py
+++ b/auth/session.py
@@ -5,3 +5,3 @@ import hmac
 def verify_token(secret: str, token: str, signature: str) -> bool:
     \"\"\"Check that a session token was signed with our secret.\"\"\"
-    expected = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
+    expected = hmac.new(secret.encode(), token.encode(), hashlib.md5).hexdigest()
"""
    llm = FakeLLM(findings_reply([]))
    Reviewer(llm, RepoAssistant(store, embedder, None)).review(diff)
    prompt = llm.calls[0][1]
    assert "api/login.py" in prompt and "calls verify_token" in prompt
