import re

import pytest

from xpt.cli import main

TESTS = """\
import os
import subprocess
import sys
import tempfile

from xpt import expect


def test_capsys(capsys):
    print("built 2 files")
    print("warning: slow", file=sys.stderr)
    expect.output(capsys)


def test_capfd(capfd):
    subprocess.run([sys.executable, "-c", "print('child')"], check=True)
    expect.output(capfd)


def test_process():
    code = "import sys; print('hi'); sys.exit(2)"
    expect.output(subprocess.run([sys.executable, "-c", code], capture_output=True))


def test_paths(capsys):
    made = tempfile.mkdtemp()
    print(f"wrote {made}/out.txt  ")
    os.rmdir(made)
    expect.output(capsys)
"""

FILLED = """\
import os
import subprocess
import sys
import tempfile

from xpt import expect


def test_capsys(capsys):
    print("built 2 files")
    print("warning: slow", file=sys.stderr)
    expect.output(capsys, {"stdout": ["built 2 files"], "stderr": ["warning: slow"]})


def test_capfd(capfd):
    subprocess.run([sys.executable, "-c", "print('child')"], check=True)
    expect.output(capfd, {"stdout": ["child"], "stderr": []})


def test_process():
    code = "import sys; print('hi'); sys.exit(2)"
    expect.output(
        subprocess.run([sys.executable, "-c", code], capture_output=True),
        {"stdout": ["hi"], "stderr": [], "returncode": 2},
    )


def test_paths(capsys):
    made = tempfile.mkdtemp()
    print(f"wrote {made}/out.txt  ")
    os.rmdir(made)
    expect.output(capsys, {"stdout": ["wrote <tmp>/out.txt"], "stderr": []})
"""


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    (pytester.path / "test_cli.py").write_text(TESTS, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    return pytester


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def source(pytester: pytest.Pytester) -> str:
    return (pytester.path / "test_cli.py").read_text(encoding="utf-8")


def test_expect_create_fills_each_source_and_the_rerun_passes(project: pytest.Pytester):
    run(project, "--expect-create").assert_outcomes(failed=4)
    assert source(project) == FILLED
    run(project).assert_outcomes(passed=4)


def test_the_report_names_the_line_that_changed_and_says_what_was_normalized(
    project: pytest.Pytester,
):
    run(project, "--expect-create").assert_outcomes(failed=4)
    changed = source(project).replace('["built 2 files"]', '["built 3 files"]')
    changed = changed.replace('"wrote <tmp>/out.txt"', '"wrote <tmp>/in.txt"')
    (project.path / "test_cli.py").write_text(changed, encoding="utf-8")
    result = run(project, "-v")
    result.assert_outcomes(passed=2, failed=2)
    text = "\n".join(result.outlines)
    assert '  value["stdout"][0]: "built 3 files" -> "built 2 files"' in text
    assert '  value["stdout"][0]: "wrote <tmp>/in.txt" -> "wrote <tmp>/out.txt"' in text
    assert (
        "  output normalized: the temporary directory -> <tmp>, trailing whitespace dropped. "
        "normalize=False compares it as printed."
    ) in text
    assert len(re.findall("output normalized", text)) == 1


def test_xpt_accept_writes_the_new_output_into_the_call(project: pytest.Pytester):
    run(project, "--expect-create").assert_outcomes(failed=4)
    changed = source(project).replace('"stderr": ["warning: slow"]', '"stderr": []')
    (project.path / "test_cli.py").write_text(changed, encoding="utf-8")
    result = run(project)
    [id] = [m[1] for line in result.outlines if (m := re.fullmatch(r"  (\w+)  mismatch", line))]
    run(project, f"--expect-accept={id}").assert_outcomes(passed=3, failed=1)
    assert source(project) == FILLED


def test_xpt_failures_says_what_was_normalized(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    run(project).assert_outcomes(failed=4)
    monkeypatch.chdir(project.path)
    capsys.readouterr()
    assert main(["failures"]) == 1
    lines = capsys.readouterr().out.splitlines()
    at = lines.index('  actual: {"stdout": ["wrote <tmp>/out.txt"], "stderr": []}')
    assert lines[at + 1] == (
        "  output normalized: the temporary directory -> <tmp>, trailing whitespace dropped. "
        "normalize=False compares it as printed."
    )
