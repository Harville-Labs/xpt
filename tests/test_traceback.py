import os
import re
import subprocess
import sys
from pathlib import Path

import crash_project
import pytest

from xpt import traceback
from xpt.summary import limits
from xpt.traceback import Hooks, collapse, library_path, locals_lines, project_root, verbosity

COUNT = re.compile(r"(?<=\()\d+(?= times\))|(?<=n = )\d+")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("XPT_TRACEBACK", raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    root = crash_project.write(tmp_path / "project")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(root), str(root / "lib/site-packages")]))
    return root


def python(root: Path, *argv: str, xpt: tuple[str, ...] | None = None, **env: str):
    """Run Python in `root`, through `xpt run` with `xpt` as its options unless None."""
    command = [sys.executable, *argv]
    if xpt is not None:
        command = [sys.executable, "-m", "xpt", "run", *xpt, "--", *command]
    return subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **env},
        check=False,
    )


def compact_and_default(root: Path, script: str):
    compact = python(root, script, xpt=())
    default = python(root, script)
    assert (compact.returncode, compact.stdout) == (default.returncode, default.stdout)
    compact_bytes = len(compact.stderr.encode("utf-8"))
    default_bytes = len(default.stderr.encode("utf-8"))
    assert compact_bytes * 3 <= default_bytes, (compact_bytes, default_bytes)
    assert "Traceback (most recent call last)" in default.stderr
    return compact


def test_a_library_error_names_the_project_frame_and_the_library_it_went_through(
    project: Path,
):
    ran = compact_and_default(project, "library.py")
    assert ran.returncode == 1
    assert ran.stderr.splitlines() == [
        "ValueError: invalid literal for int() with base 10: 'x'",
        "  at app/load.py:6 in read_row",
        "  > return fakelib.parse(count)",
        "  via fakelib/core.py",
        "  from app/load.py:12 in load, library.py:4 in <module>",
        "  locals:",
        '    row = {"count": "x"}',
        '    count = "x"',
    ]


def test_a_chained_error_gives_one_line_per_link(project: Path):
    ran = compact_and_default(project, "chained.py")
    assert ran.returncode == 1
    assert ran.stderr.splitlines() == [
        "RuntimeError: load failed",
        "  at app/chain.py:12 in load",
        '  > raise RuntimeError("load failed")',
        "  from chained.py:3 in <module>",
        "  during ValueError: no count at app/chain.py:5",
        "  caused by KeyError: 'count' at app/chain.py:3",
        "  locals:",
        '    row = {"name": "ada"}',
    ]


def test_a_recursion_error_counts_the_repeated_frames(project: Path):
    ran = compact_and_default(project, "recursion.py")
    assert ran.returncode == 1
    assert [COUNT.sub("N", line) for line in ran.stderr.splitlines()] == [
        "RecursionError: maximum recursion depth exceeded",
        "  at app/deep.py:2 in depth",
        "  > return depth(n + 1)",
        "  from app/deep.py:2 in depth (N times), recursion.py:5 in main, "
        "recursion.py:8 in <module>",
        "  locals:",
        "    n = N",
    ]


def test_a_thread_error_keeps_the_thread_line_and_the_exit_code(project: Path):
    ran = compact_and_default(project, "thread.py")
    assert (ran.returncode, ran.stdout) == (0, "done\n")
    assert ran.stderr.splitlines() == [
        "Exception in thread worker:",
        "ValueError: invalid literal for int() with base 10: 'x'",
        "  at app/load.py:6 in read_row",
        "  > return fakelib.parse(count)",
        "  via fakelib/core.py",
        "  from thread.py:7 in work",
        "  locals:",
        '    row = {"count": "x"}',
        '    count = "x"',
    ]


def test_very_verbose_prints_python_traceback_unchanged(project: Path):
    full = python(project, "chained.py", xpt=("-vv",))
    default = python(project, "chained.py")
    assert (full.returncode, full.stdout, full.stderr) == (
        default.returncode,
        default.stdout,
        default.stderr,
    )


@pytest.mark.parametrize("mode", ["full", "0"])
def test_xpt_traceback_full_or_zero_keeps_python_traceback(project: Path, mode: str):
    ran = python(project, "library.py", XPT_TRACEBACK=mode, PYTHONPATH=_with_startup())
    assert ran.stderr == python(project, "library.py").stderr


def test_install_honors_xpt_traceback(project: Path):
    code = "import xpt.traceback as t; print(t.install()); 1/0"
    off = python(project, "-c", code)
    on = python(project, "-c", code, XPT_TRACEBACK="1")
    assert (off.stdout, off.returncode) == ("False\n", 1)
    assert "Traceback (most recent call last)" in off.stderr
    assert (on.stdout, on.returncode) == ("True\n", 1)
    assert on.stderr.splitlines() == [
        "ZeroDivisionError: division by zero",
        "  at <string>:1 in <module>",
    ]


def test_a_sitecustomize_already_on_the_path_still_runs(project: Path, tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "sitecustomize.py").write_text("THEIRS = True\n", encoding="utf-8")
    code = (
        "import sitecustomize, sys; print(sitecustomize.THEIRS, sys.path.count(sys.argv[1])); 1/0"
    )
    startup = str(traceback.STARTUP)
    ran = python(project, "-c", code, startup, xpt=(), PYTHONPATH=str(other))
    assert ran.stdout == "True 0\n"
    assert ran.stderr.splitlines()[0] == "ZeroDivisionError: division by zero"


def test_xpt_run_returns_the_command_exit_code(project: Path):
    ran = python(project, "-c", "raise SystemExit(3)", xpt=())
    assert (ran.returncode, ran.stderr) == (3, "")


def test_xpt_run_without_a_command_is_a_usage_error(project: Path):
    ran = subprocess.run(
        [sys.executable, "-m", "xpt", "run"], capture_output=True, text=True, check=False
    )
    assert ran.returncode == 2
    assert "xpt run needs a command" in ran.stderr


def test_xpt_run_reports_a_command_it_cannot_start(project: Path):
    ran = subprocess.run(
        [sys.executable, "-m", "xpt", "run", "--", str(project / "no-such-command")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 127
    assert ran.stderr.startswith("xpt: error: could not run ")


def test_a_failing_formatter_falls_back_to_python_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    def broken(self: Hooks, error: BaseException, tb: object) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(Hooks, "lines", broken)
    hooks = Hooks(tmp_path, tmp_path, limits(0))
    try:
        raise ValueError("bad")
    except ValueError as error:
        hooks.excepthook(ValueError, error, error.__traceback__)
    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err
    assert "ValueError: bad" in err
    assert err.splitlines()[-1].startswith(
        "xpt: the compact traceback failed (RuntimeError: boom), so this is Python's traceback."
    )


def test_verbose_raises_the_limits(tmp_path: Path):
    def fail() -> None:
        raise ValueError("x" * 300)

    try:
        fail()
    except ValueError as error:
        quiet = Hooks(tmp_path, tmp_path, limits(0)).lines(error, error.__traceback__)
        loud = Hooks(tmp_path, tmp_path, limits(1)).lines(error, error.__traceback__)
    assert quiet[0] == f"ValueError: {'x' * 188}... (312 chars)"
    assert loud[0] == f"ValueError: {'x' * 300}"


def test_a_value_xpt_cannot_record_prints_its_type_only(tmp_path: Path):
    class Handle:
        pass

    def fail(handle: object, items: list[int]) -> None:
        raise ValueError("bad")

    try:
        fail(Handle(), list(range(100)))
    except ValueError as error:
        tb = error.__traceback__
        assert tb is not None and tb.tb_next is not None
        lines = locals_lines(tb.tb_next.tb_frame, limits(0))
    assert lines[0:2] == ["  locals:", f"    handle = <{Handle.__module__}.{Handle.__qualname__}>"]
    assert lines[2].startswith("    items = [0, 1, 2")
    assert lines[2].endswith("chars)")
    assert "0x" not in "\n".join(lines)


def test_an_exception_group_lists_its_members(tmp_path: Path):
    def fail() -> None:
        raise ExceptionGroup("two failed", [ValueError("a"), KeyError("b")])

    try:
        fail()
    except ExceptionGroup as error:
        lines = Hooks(tmp_path, tmp_path, limits(0)).lines(error, error.__traceback__)
    assert lines[0] == "ExceptionGroup: two failed (2 sub-exceptions)"
    assert lines[-2:] == ["  member ValueError: a", "  member KeyError: 'b'"]


def test_collapse_counts_repeated_frames_and_cycles():
    assert collapse(["main", *["a", "b"] * 3, "a"]) == ["main", "a, b (3 times)", "a"]
    assert collapse(["f"] * 5 + ["g"]) == ["f (5 times)", "g"]
    assert collapse(["a", "b", "c"]) == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("mode", "level"),
    [(None, None), ("", None), ("0", None), ("full", None), ("1", 0), ("verbose", 1)],
)
def test_xpt_traceback_values(mode: str | None, level: int | None):
    assert verbosity(mode) == level


def test_the_project_root_is_the_nearest_pyproject_or_git_directory(tmp_path: Path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "pkg" / "pyproject.toml").parent.mkdir()
    (tmp_path / "repo" / "pkg" / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "repo" / "pkg" / "src").mkdir()
    assert project_root(tmp_path / "repo" / "pkg" / "src") == tmp_path / "repo" / "pkg"
    (tmp_path / "repo" / "docs").mkdir()
    assert project_root(tmp_path / "repo" / "docs") == tmp_path / "repo"
    (tmp_path / "bare").mkdir()
    assert project_root(tmp_path / "bare") in (tmp_path / "bare", *(tmp_path / "bare").parents)


def test_a_library_frame_prints_relative_to_its_import_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(sys, "path", [str(tmp_path), str(tmp_path / "site-packages")])
    assert library_path(tmp_path / "site-packages" / "requests" / "api.py") == "requests/api.py"
    assert library_path(Path("/elsewhere/mod.py").absolute()) == "mod.py"


def _with_startup() -> str:
    return os.pathsep.join([str(traceback.STARTUP), os.environ["PYTHONPATH"]])
