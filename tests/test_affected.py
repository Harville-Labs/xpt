"""`pytest --xpt-map` records the lines every test ran, and `xpt affected` selects the tests
that ran the lines `git diff` shows changed, in a fixture repository."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cli_support import xpt

from xpt.affected import NO_MAP
from xpt.changes import Hunk, parse

SHOP = """\
TAX = 0.0


def total(price):
    rate = 0.1
    return round(price * (1 - rate) * (1 + TAX), 2)


def shipping():
    return 5
"""

LIMITS = """\
LIMIT = 3
"""

TEST_SHOP = """\
from shop import shipping, total


def test_total():
    assert total(100) == 90.0


def test_shipping():
    assert shipping() == 5
"""

TEST_OTHER = """\
from limits import LIMIT


def test_plain():
    assert 1 + 1 == 2


def test_limit():
    assert LIMIT == 3
"""

TOTAL = "test_shop.py::test_total"
SHIPPING = "test_shop.py::test_shipping"
ARGV = "import sys; print(sys.argv[1:]); sys.exit(3)"


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
    """A repository with the shop and its tests committed on main, and no map yet."""
    files = {
        "shop.py": SHOP,
        "limits.py": LIMITS,
        "test_shop.py": TEST_SHOP,
        "test_other.py": TEST_OTHER,
        ".gitignore": "runpytest-*\n/stdout\n/stderr\n",
    }
    for name, text in files.items():
        (pytester.path / name).write_text(text, encoding="utf-8")
    pytester.makeini("[pytest]\npythonpath = .\n")
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    git(pytester.path, "init", "-q", "-b", "main")
    git(pytester.path, "add", ".")
    git(pytester.path, "commit", "-q", "-m", "start")
    return pytester


def build_map(project: pytest.Pytester, *args: str) -> None:
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "--xpt-map", *args)
    result.assert_outcomes(passed=4)


def edit(project: pytest.Pytester, name: str, old: str, new: str) -> None:
    path = project.path / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")
    # A same-size edit within the same second leaves the cached bytecode looking fresh, so
    # move the mtime forward to make Python and pytest's assertion rewriter recompile.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def selected(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, str]:
    data = xpt(capsys, "affected", "--json", *args).json
    assert data["all"] is False, data["reason"]
    return {t["id"]: t["reason"] for t in data["tests"]}


def everything(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    data = xpt(capsys, "affected", "--json", *args).json
    assert data["all"] is True
    assert data["tests"] == []
    return data["reason"]


def test_parse_keeps_the_base_line_of_each_hunk():
    diff = "+++ a.py\n@@ -5 +5,2 @@\n-x\n+y\n+z\n@@ -9,0 +11,1 @@\n+w\n@@ -20,2 +22,0 @@\n-a\n-b\n"
    assert parse(diff) == [
        Hunk("a.py", 5, 2, ("x",), ("y", "z"), 5),
        Hunk("a.py", 11, 1, (), ("w",), 9),
        Hunk("a.py", 22, 0, ("a", "b"), (), 20),
    ]


def test_editing_one_function_selects_only_the_tests_that_call_it(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "rate = 0.1", "rate = 0.2")
    assert selected(capsys) == {TOTAL: "ran shop.py:total, which changed"}
    lines = xpt(capsys, "affected").out.splitlines()
    assert lines[0].startswith("1 test ran the code changed since ")
    assert lines[1:] == [
        f"  {TOTAL}  ran shop.py:total, which changed",
        "Next: xpt affected -- pytest",
    ]


def test_a_line_added_anywhere_in_a_function_selects_the_tests_that_ran_it(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "    return 5\n", "    print()\n\n    return 5\n    print()\n")
    assert selected(capsys) == {SHIPPING: "ran shop.py:shipping, which changed"}


def test_a_deleted_module_selects_the_tests_that_ran_it(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    (project.path / "shop.py").unlink()
    reason = "ran shop.py, which was deleted"
    assert selected(capsys) == {TOTAL: reason, SHIPPING: reason}


def test_a_comment_or_a_blank_line_selects_nothing(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "    rate = 0.1\n", "    # the rate\n    rate = 0.1\n\n")
    assert selected(capsys) == {}
    assert xpt(capsys, "affected").out.startswith("No test ran the code changed since ")


def test_a_module_level_edit_selects_every_test_that_ran_a_line_of_the_file(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "TAX = 0.0", "TAX = 0.5")
    reason = "ran shop.py, whose import-time code changed at line 1"
    assert selected(capsys) == {TOTAL: reason, SHIPPING: reason}


def test_an_import_time_edit_no_test_ran_a_line_of_runs_everything(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "limits.py", "LIMIT = 3", "LIMIT = 4")
    assert everything(capsys) == (
        "limits.py:1 changed code that runs at import, and no test in the map ran a line of "
        "limits.py"
    )


def test_a_changed_test_file_selects_the_whole_file(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "test_other.py", "1 + 1 == 2", "2 + 2 == 4")
    assert selected(capsys) == {"test_other.py": "test file changed"}


def test_a_new_test_file_is_always_selected(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    (project.path / "test_new.py").write_text("def test_new():\n    pass\n", encoding="utf-8")
    assert selected(capsys) == {"test_new.py": "new test file"}


@pytest.mark.parametrize("name", ["conftest.py", "pyproject.toml", "data.json"])
def test_configuration_or_a_non_python_change_runs_everything(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str], name: str
):
    build_map(project)
    (project.path / name).write_text("", encoding="utf-8")
    reason = everything(capsys)
    assert reason.startswith(f"{name} changed, ")


def test_without_a_map_everything_runs(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    assert everything(capsys) == NO_MAP
    assert xpt(capsys, "affected").out == f"Run every test: {NO_MAP}.\n"


def test_a_file_changed_outside_the_diff_makes_the_map_stale(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "rate = 0.1", "rate = 0.2")
    git(project.path, "commit", "-q", "-am", "regress")
    assert selected(capsys) == {TOTAL: "ran shop.py:total, which changed"}
    assert everything(capsys, "--since", "HEAD") == (
        "map is stale: 1 file changed since it was built that the diff against HEAD doesn't "
        "show (shop.py); rebuild it with pytest --xpt-map"
    )


def test_without_git_everything_runs(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    build_map(project)
    monkeypatch.setenv("PATH", str(project.path / "nowhere"))
    assert everything(capsys) == "git is not installed"


def test_the_map_is_one_file_built_from_every_xdist_worker(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    pytest.importorskip("xdist")
    build_map(project, "-n", "2")
    xpt_dir = project.path / ".xpt"
    stored = json.loads((xpt_dir / "map.json").read_text(encoding="utf-8"))
    assert sorted(stored["tests"]) == [
        "test_other.py::test_limit",
        "test_other.py::test_plain",
        SHIPPING,
        TOTAL,
    ]
    assert sorted(stored["files"]) == ["limits.py", "shop.py", "test_other.py", "test_shop.py"]
    assert not (xpt_dir / "map").exists()
    edit(project, "shop.py", "rate = 0.1", "rate = 0.2")
    assert selected(capsys) == {TOTAL: "ran shop.py:total, which changed"}


def test_a_failed_session_still_replaces_the_map_and_an_interrupted_one_does_not(
    project: pytest.Pytester,
):
    build_map(project)
    stored = project.path / ".xpt" / "map.json"
    before = stored.read_text(encoding="utf-8")
    edit(project, "test_other.py", "1 + 1 == 2", "1 + 1 == 3")
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "--xpt-map")
    result.assert_outcomes(passed=3, failed=1)
    after = stored.read_text(encoding="utf-8")
    assert after != before
    (project.path / "test_stop.py").write_text(
        "def test_stop():\n    raise KeyboardInterrupt\n", encoding="utf-8"
    )
    project.runpytest_subprocess("-p", "no:cacheprovider", "--xpt-map")
    assert stored.read_text(encoding="utf-8") == after
    assert not (project.path / ".xpt" / "map").exists()


def test_running_passes_the_selected_ids_and_exits_as_the_command_does(
    project: pytest.Pytester, capfd: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "rate = 0.1", "rate = 0.2")
    ran = xpt(capfd, "affected", "--", sys.executable, "-c", ARGV)
    assert ran.code == 3
    assert ran.out.splitlines()[-1] == str([TOTAL])


def test_running_pytest_runs_only_the_selected_tests(
    project: pytest.Pytester, capfd: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "rate = 0.1", "rate = 0.2")
    ran = xpt(capfd, "affected", "--", "pytest", "-p", "no:cacheprovider", "--xpt-report=pytest")
    assert ran.code == 1
    assert "1 failed in" in ran.out
    assert "passed" not in ran.out


def test_running_with_nothing_selected_runs_nothing_unless_asked(
    project: pytest.Pytester, capfd: pytest.CaptureFixture[str]
):
    build_map(project)
    edit(project, "shop.py", "    rate = 0.1\n", "    # the rate\n    rate = 0.1\n")
    ran = xpt(capfd, "affected", "--", sys.executable, "-c", ARGV)
    assert ran.code == 0
    assert ran.out.splitlines()[-1] == "Nothing to run."
    ran = xpt(capfd, "affected", "--all-if-empty", "--", sys.executable, "-c", ARGV)
    assert ran.code == 3
    assert ran.out.splitlines()[-1] == "[]"


def test_running_everything_passes_no_ids(
    project: pytest.Pytester, capfd: pytest.CaptureFixture[str]
):
    ran = xpt(capfd, "affected", "--", sys.executable, "-c", ARGV)
    assert ran.code == 3
    assert ran.out.splitlines() == [f"Run every test: {NO_MAP}.", "[]"]
