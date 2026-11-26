"""`xpt show` and `xpt accept` print the changed code a failing test ran, from a run with
--xpt-trace and `git diff`, in a fixture repository with a seeded regression."""

import ast
import shutil
import subprocess
from pathlib import Path

import pytest
from cli_support import xpt

from xpt.changes import Hunk, function_at, parse

SHOP = """\
TAX = 0.0


def total(price):
    rate = 0.1
    return round(price * (1 - rate) * (1 + TAX), 2)


def shipping():
    return 5
"""

TESTS = """\
from xpt import expect

from shop import shipping, total


def test_total():
    expect(total(100), 90.0)


def test_shipping():
    expect(shipping(), 4)
"""

DIFF = """\
diff --git src/a.py src/a.py
index 1..2 100644
--- src/a.py
+++ src/a.py
@@ -5 +5,2 @@ def total(price):
-    rate = 0.1
+    rate = 0.2
+    rate += 0
@@ -9,2 +10,0 @@ def gone():
-    a = 1
-    b = 2
diff --git src/old.py src/old.py
deleted file mode 100644
--- src/old.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""


def test_parse_reads_new_side_lines_and_removals():
    assert parse(DIFF) == [
        Hunk("src/a.py", 5, 2, ("    rate = 0.1",), ("    rate = 0.2", "    rate += 0"), 5),
        Hunk("src/a.py", 10, 0, ("    a = 1", "    b = 2"), (), 9),
    ]


def test_a_removal_counts_as_run_when_a_line_beside_it_ran():
    removal = Hunk("a.py", 9, 0, ("x",), ())
    assert removal.ran(frozenset({10}))
    assert removal.ran(frozenset({9}))
    assert not removal.ran(frozenset({8, 11}))


def test_function_at_names_the_innermost_function_with_its_class():
    tree = ast.parse(
        "class A:\n    @staticmethod\n    def f():\n        def g():\n            pass\n"
        "        return g\nX = 1\n"
    )
    assert function_at(tree, 2) == "A.f"
    assert function_at(tree, 5) == "A.f.g"
    assert function_at(tree, 7) is None


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    """A repository with SHOP and TESTS committed on main."""
    (pytester.path / "shop.py").write_text(SHOP, encoding="utf-8")
    (pytester.path / "test_shop.py").write_text(TESTS, encoding="utf-8")
    pytester.makeini("[pytest]\npythonpath = .\n")
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    git(pytester.path, "init", "-q", "-b", "main")
    git(pytester.path, "add", ".")
    git(pytester.path, "commit", "-q", "-m", "start")
    return pytester


def regress(project: pytest.Pytester) -> None:
    shop = project.path / "shop.py"
    shop.write_text(shop.read_text().replace("rate = 0.1", "rate = 0.2"), encoding="utf-8")


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "--xpt-trace", *args)


def ids(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    groups = xpt(capsys, "failures", "--json").json["groups"]
    return {g["site"]["qualname"]: g["id"] for g in groups}


def test_show_prints_the_changed_hunk_the_failing_test_ran(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    regress(project)
    run(project).assert_outcomes(failed=2)
    found = ids(capsys)
    shown = xpt(capsys, "show", found["test_total"])
    assert shown.code == 0
    lines = shown.out.splitlines()
    start = lines.index("  changed code this test ran (since main):")
    assert lines[start + 1 : start + 4] == [
        "    shop.py:5 total",
        "      - rate = 0.1",
        "      + rate = 0.2",
    ]


def test_show_says_no_changed_code_ran_when_the_test_ran_none(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    regress(project)
    run(project).assert_outcomes(failed=2)
    shown = xpt(capsys, "show", ids(capsys)["test_shipping"])
    assert (
        "  no changed code ran; the expectation or the environment changed (diff against main)"
        in shown.out.splitlines()
    )


def test_show_json_carries_the_hunks(project: pytest.Pytester, capsys: pytest.CaptureFixture[str]):
    regress(project)
    run(project).assert_outcomes(failed=2)
    group = xpt(capsys, "show", ids(capsys)["test_total"], "--json").json["group"]
    assert group["changed_code"] == {
        "status": "ran",
        "since": "main",
        "functions": [
            {
                "path": "shop.py",
                "function": "total",
                "hunks": [
                    {
                        "line": 5,
                        "count": 1,
                        "removed": ["    rate = 0.1"],
                        "added": ["    rate = 0.2"],
                    }
                ],
            }
        ],
        "stale": [],
        "note": "",
    }


def test_a_changed_constant_counts_for_a_test_that_ran_its_module(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    shop = project.path / "shop.py"
    shop.write_text(SHOP.replace("TAX = 0.0", "TAX = 0.5"), encoding="utf-8")
    run(project).assert_outcomes(failed=2)
    shown = xpt(capsys, "show", ids(capsys)["test_total"]).out.splitlines()
    assert "    shop.py:1 <module>" in shown
    assert "      + TAX = 0.5" in shown


def test_since_diffs_against_another_ref_and_includes_uncommitted_changes(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    git(project.path, "checkout", "-q", "-b", "work")
    regress(project)
    git(project.path, "commit", "-q", "-am", "regress")
    run(project).assert_outcomes(failed=2)
    total = ids(capsys)["test_total"]
    assert "  changed code this test ran (since main):" in xpt(capsys, "show", total).out
    shown = xpt(capsys, "show", total, "--since", "HEAD").out.splitlines()
    assert (
        "  no changed code ran; the expectation or the environment changed (diff against HEAD)"
        in shown
    )


def test_an_untracked_file_counts_as_changed(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / "rates.py").write_text("RATE = 0.2\n", encoding="utf-8")
    shop = project.path / "shop.py"
    shop.write_text(SHOP.replace("    rate = 0.1\n", "    from rates import RATE as rate\n"))
    git(project.path, "add", "shop.py")
    run(project).assert_outcomes(failed=2)
    shown = xpt(capsys, "show", ids(capsys)["test_total"]).out.splitlines()
    assert "      + from rates import RATE as rate" in shown
    assert "    rates.py:1 <module>" in shown


def test_a_file_edited_after_the_run_is_left_out(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    regress(project)
    run(project).assert_outcomes(failed=2)
    (project.path / "shop.py").write_text(SHOP.replace("rate = 0.1", "rate = 0.3"))
    shown = xpt(capsys, "show", ids(capsys)["test_total"]).out.splitlines()
    assert "  changed since the run, left out: shop.py" in shown


def test_accept_prints_the_changed_code_before_it_writes(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    regress(project)
    run(project).assert_outcomes(failed=2)
    found = ids(capsys)
    accepted = xpt(capsys, "accept", found["test_total"], found["test_shipping"])
    lines = accepted.out.splitlines()
    assert lines[1:5] == [
        "  changed code this test ran (since main):",
        "    shop.py:5 total",
        "      - rate = 0.1",
        "      + rate = 0.2",
    ]
    assert lines[6].startswith("  no changed code ran;")
    data = xpt(capsys, "accept", "--all", "--dry-run", "--json").json
    found = [o["changed_code"] for o in data["outcomes"]]
    assert [(c["status"], c["stale"]) for c in found] == [
        ("ran", ["test_shop.py"]),
        ("none_ran", ["test_shop.py"]),
    ]


def test_without_a_trace_show_says_to_rerun_with_xpt_trace(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    regress(project)
    project.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=2)
    shown = xpt(capsys, "show", ids(capsys)["test_total"])
    assert (
        "  no trace of these tests; run them with pytest --xpt-trace to see the changed code "
        "they ran" in shown.out.splitlines()
    )
    assert shown.code == 0


def test_outside_a_repository_show_prints_one_note(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (pytester.path / "shop.py").write_text(SHOP, encoding="utf-8")
    (pytester.path / "test_shop.py").write_text(TESTS, encoding="utf-8")
    pytester.makeini("[pytest]\npythonpath = .\n")
    run(pytester).assert_outcomes(failed=1, passed=1)
    shown = xpt(capsys, "show", ids(capsys)["test_shipping"])
    assert shown.code == 0
    assert "  changed code: unknown, not a git repository." in shown.out.splitlines()


def test_without_git_show_prints_one_note(
    project: pytest.Pytester,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    regress(project)
    run(project).assert_outcomes(failed=2)
    found = ids(capsys)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    shown = xpt(capsys, "show", found["test_total"])
    assert "  changed code: unknown, git is not installed." in shown.out.splitlines()
    data = xpt(capsys, "accept", "--all", "--dry-run").out.splitlines()
    assert data.count("changed code: unknown, git is not installed.") == 1


def test_under_xdist_each_worker_stores_its_own_trace(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    pytest.importorskip("xdist")
    regress(project)
    split = TESTS.replace("from xpt import expect\n", "import pytest\nfrom xpt import expect\n")
    for name in ("total", "shipping"):
        split = split.replace(
            f"def test_{name}", f"@pytest.mark.xdist_group({name!r})\ndef test_{name}"
        )
    (project.path / "test_shop.py").write_text(split, encoding="utf-8")
    run(project, "-n", "2", "--dist", "loadgroup").assert_outcomes(failed=2)
    [run_dir] = (project.path / ".xpt" / "runs").iterdir()
    assert sorted(p.name for p in run_dir.glob("*.trace.json")) == [
        "gw0.trace.json",
        "gw1.trace.json",
    ]
    shown = xpt(capsys, "show", ids(capsys)["test_total"]).out.splitlines()
    assert "      + rate = 0.2" in shown


def test_xpt_trace_needs_recording(project: pytest.Pytester):
    result = run(project, "--expect-no-record")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--xpt-trace needs recording. Drop --expect-no-record.*"])


def test_an_unknown_since_ref_is_a_note(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    regress(project)
    run(project).assert_outcomes(failed=2)
    shown = xpt(capsys, "show", ids(capsys)["test_total"], "--since", "nope")
    assert shown.code == 0
    assert "  changed code: unknown, nope is not a commit." in shown.out.splitlines()
