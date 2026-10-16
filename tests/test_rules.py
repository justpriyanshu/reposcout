import pytest

from reposcout.review.diff import parse_unified_diff
from reposcout.review.rules import is_test_path, run_rules


def diff_for(path: str, *added: str) -> str:
    body = "\n".join(f"+{line}" for line in added)
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,{len(added)} @@\n{body}\n"


def rule_ids(path: str, *added: str) -> set[str]:
    (file,) = parse_unified_diff(diff_for(path, *added))
    return {f.source.removeprefix("rule:") for f in run_rules(file)}


@pytest.mark.parametrize(
    "line, rule",
    [
        ('cur.execute(f"SELECT * FROM t WHERE id = {uid}")', "sql-format"),
        ('cur.execute("SELECT * FROM t WHERE id = %s" % uid)', "sql-format"),
        ('API_KEY = "abcd1234efgh5678"', "hardcoded-secret"),
        ('aws = "AKIAABCDEFGHIJKLMNOP"', "aws-key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key"),
        ("result = eval(user_input)", "python-eval"),
        ("subprocess.run(cmd, shell=True)", "shell-true"),
        ("requests.get(url, verify=False)", "tls-verify-off"),
        ("data = yaml.load(stream)", "yaml-load"),
        ("obj = pickle.loads(blob)", "pickle-load"),
        ("except:", "bare-except"),
        ("def add(item, items=[]):", "mutable-default"),
        ("    breakpoint()", "breakpoint"),
        ("# TODO: handle errors", "todo"),
    ],
)
def test_python_rules_fire(line, rule):
    assert rule in rule_ids("app/module.py", line)


def test_javascript_rules_fire():
    found = rule_ids("web/app.js", "el.innerHTML = html;", "console.log(x);", "eval(code);", "debugger;")
    assert {"inner-html", "console-log", "js-eval", "breakpoint"} <= found


@pytest.mark.parametrize(
    "line",
    [
        'cur.execute("SELECT * FROM t WHERE id = ?", (uid,))',
        "data = yaml.load(stream, Loader=yaml.SafeLoader)",
        "value = ast.literal_eval(text)",
        "except ValueError:",
        "def add(item, items=None):",
        "token = os.environ['API_TOKEN']",
        'if el.innerHTML == "":',
    ],
)
def test_safe_code_is_not_flagged(line):
    assert rule_ids("app/module.py", line) == set()


def test_language_specific_rules_do_not_leak():
    assert "console-log" not in rule_ids("app/module.py", "console.log(x)")
    assert "bare-except" not in rule_ids("web/app.js", "except:")


def test_secret_rule_skips_test_files():
    assert rule_ids("tests/test_login.py", 'password = "hunter2hunter2"') == set()
    assert "hardcoded-secret" in rule_ids("app/login.py", 'password = "hunter2hunter2"')


def test_is_test_path():
    assert is_test_path("tests/test_x.py")
    assert is_test_path("src/foo_test.go")
    assert is_test_path("web/button.spec.ts")
    assert not is_test_path("src/latest.py")
    assert not is_test_path("app/contest/views.py")
