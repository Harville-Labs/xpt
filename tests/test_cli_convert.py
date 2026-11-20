import os
from pathlib import Path

import pytest
from cli_support import Ran, xpt

PASSING = "def test_ok():\n    assert sorted([2, 1]) == [1, 2]\n"
FAILING = "def test_bad():\n    assert {'n': 1 + 1} == {'n': 3}\n"
SKIPPED = "def test_other():\n    assert len('ab') == len('cd')\n"


def write(root: Path, path: str, text: str) -> Path:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    return file


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "tests/test_a.py", PASSING + "\n\n" + SKIPPED)
    write(tmp_path, "tests/sub/b_test.py", FAILING)
    write(tmp_path, "tests/helpers.py", PASSING)
    write(tmp_path, "tests/.hidden/test_c.py", PASSING)
    return tmp_path


def test_convert_rewrites_test_files_under_a_directory_and_reports_skips(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "convert", "tests")
    assert ran == Ran(
        0,
        "tests/test_a.py:6  skipped  neither side is a literal.\n"
        "Converted 2 asserts in tests/sub/b_test.py, tests/test_a.py. 1 skipped. "
        "Next: run the tests.\n",
        "",
    )
    assert (project / "tests/test_a.py").read_text(encoding="utf-8") == (
        "from xpt import expect\ndef test_ok():\n    expect(sorted([2, 1]), [1, 2])\n\n\n" + SKIPPED
    )
    assert (project / "tests/helpers.py").read_text(encoding="utf-8") == PASSING
    assert (project / "tests/.hidden/test_c.py").read_text(encoding="utf-8") == PASSING


def test_convert_converts_a_file_named_on_the_command_line_whatever_its_name(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "convert", "tests/helpers.py", "--json")
    assert ran.code == 0
    assert ran.json == {
        "schema_version": 1,
        "command": "convert",
        "dry_run": False,
        "converted": [{"path": "tests/helpers.py", "line": 2}],
        "skipped": [],
        "changed": ["tests/helpers.py"],
    }


def test_a_dry_run_reports_the_same_and_writes_nothing(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    before = (project / "tests/test_a.py").read_bytes()
    ran = xpt(capsys, "convert", "tests/test_a.py", "--dry-run")
    assert ran.out.splitlines()[-1] == (
        "Dry run, would convert 1 assert in tests/test_a.py. 1 skipped. "
        "Next: xpt convert tests/test_a.py"
    )
    assert (project / "tests/test_a.py").read_bytes() == before


def test_nothing_to_convert_says_so(project: Path, capsys: pytest.CaptureFixture[str]):
    write(project, "tests/test_none.py", "def test_it():\n    assert True\n")
    ran = xpt(capsys, "convert", "tests/test_none.py")
    assert ran == Ran(0, "Nothing to convert.\n", "")


def test_a_path_outside_the_root_is_refused_and_nothing_is_written(
    project: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
):
    outside = write(tmp_path_factory.mktemp("elsewhere"), "test_x.py", PASSING)
    before = (project / "tests/test_a.py").read_bytes()
    ran = xpt(capsys, "convert", "tests", str(outside))
    assert ran.code == 2
    assert ran.err == (
        f"xpt: error: {outside} is outside the root {project.resolve()}. "
        "Run xpt convert from the project root or pass --root.\n"
    )
    assert (project / "tests/test_a.py").read_bytes() == before


def test_a_missing_path_is_a_usage_error(project: Path, capsys: pytest.CaptureFixture[str]):
    ran = xpt(capsys, "convert", "tests/nope.py")
    assert ran == Ran(2, "", "xpt: error: tests/nope.py does not exist.\n")


@pytest.mark.skipif(os.name == "nt", reason="file modes are POSIX")
def test_a_converted_file_keeps_its_mode(project: Path, capsys: pytest.CaptureFixture[str]):
    file = project / "tests/test_a.py"
    file.chmod(0o640)
    xpt(capsys, "convert", "tests/test_a.py")
    assert file.stat().st_mode & 0o777 == 0o640


def test_converted_tests_pass_and_fail_as_the_asserts_did(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    pytester.makepyfile(test_it=PASSING + "\n\n" + FAILING)
    pytester.makeini("[pytest]\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1, failed=1)
    assert xpt(capsys, "convert", "test_it.py").code == 0
    ran = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    ran.assert_outcomes(passed=1, failed=1)
    assert 'value["n"]: 3 -> 2' in ran.stdout.str()
