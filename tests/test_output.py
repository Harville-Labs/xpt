import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import pytest

from xpt import expect
from xpt.output import Normalizer

NOTE = "  output normalized: {}. normalize=False compares it as printed."


def failure(source: object, *expected: object, **options: Any) -> str:
    with pytest.raises(AssertionError) as info:
        expect.output(source, *expected, **options)
    return str(info.value)


def completed(stdout: object = "", stderr: object = "", returncode: int = 0) -> object:
    return subprocess.CompletedProcess(["tool"], returncode, stdout, stderr)


def test_captured_output_is_recorded_as_lines_of_each_stream(capsys: pytest.CaptureFixture[str]):
    print("one")
    print("two")
    print("oops", file=sys.stderr)
    assert failure(capsys) == (
        'expect() has no expected value. Actual: {"stdout": ["one", "two"], "stderr": ["oops"]}'
    )


def test_equal_output_passes(capsys: pytest.CaptureFixture[str]):
    print("one")
    expect.output(capsys, {"stdout": ["one"], "stderr": []})


def test_a_changed_line_is_named_by_its_stream_and_index(capsys: pytest.CaptureFixture[str]):
    print("a\nb\nc")
    assert failure(capsys, {"stdout": ["a", "B", "c"], "stderr": []}) == textwrap.dedent("""\
        expect() mismatch:
          value["stdout"][1]: "B" -> "b"
          1 changed, 2 unchanged""")


def test_reading_consumes_the_output_like_readouterr(capsys: pytest.CaptureFixture[str]):
    print("once")
    expect.output(capsys, {"stdout": ["once"], "stderr": []})
    expect.output(capsys, {"stdout": [], "stderr": []})


def test_capfd_reads_output_written_to_the_file_descriptors(capfd: pytest.CaptureFixture[str]):
    subprocess.run([sys.executable, "-c", "print('from a child')"], check=True)
    expect.output(capfd, {"stdout": ["from a child"], "stderr": []})


def test_the_project_root_and_trailing_whitespace_are_normalized_and_said(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    print(f"wrote {tmp_path.as_posix()}/out.txt   ")
    assert failure(capsys, {"stdout": [], "stderr": []}).splitlines()[1:] == [
        '  value["stdout"][0]: added "wrote <root>/out.txt"',
        "  1 changed, 0 unchanged",
        NOTE.format("the project root -> <root>, trailing whitespace dropped"),
    ]


def test_a_temporary_directory_becomes_tmp(capsys: pytest.CaptureFixture[str]):
    made = tempfile.mkdtemp()
    try:
        print(Path(made) / "cache.db")
        print(tempfile.gettempdir())
        expect.output(capsys, {"stdout": [f"<tmp>{os.sep}cache.db", "<tmp>"], "stderr": []})
    finally:
        Path(made).rmdir()


def test_a_pytest_tmp_path_becomes_tmp_whatever_its_run_number():
    temp = Path(tempfile.gettempdir())
    rules = Normalizer.for_root(Path("/home/ada/proj"))
    for n in (3, 41):
        made = temp / "pytest-of-ada" / f"pytest-{n}" / "test_build0" / "out.txt"
        assert rules.apply(f"wrote {made}") == f"wrote <tmp>{os.sep}out.txt"


def test_a_path_that_only_starts_like_the_root_is_left_alone():
    rules = Normalizer.for_root(Path("/home/ada/proj"))
    line = "/home/ada/proj/a.py /home/ada/project2 /home/ada/proj."
    assert rules.apply(line) == "<root>/a.py /home/ada/project2 <root>."


def test_normalize_false_compares_the_output_as_printed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    print(f"{tmp_path}  ")
    expect.output(capsys, {"stdout": [f"{tmp_path}  "], "stderr": []}, normalize=False)


def test_output_that_needs_no_normalizing_has_no_note(capsys: pytest.CaptureFixture[str]):
    print("plain")
    assert "normalized" not in failure(capsys, {"stdout": [], "stderr": []})


def test_a_completed_process_also_records_its_returncode():
    run = subprocess.run(
        [sys.executable, "-c", "import sys; print('hi'); sys.exit(3)"],
        capture_output=True,
        text=True,
    )
    expect.output(run, {"stdout": ["hi"], "stderr": [], "returncode": 3})


def test_bytes_output_is_decoded_as_utf8_with_escapes_for_bad_bytes():
    expect.output(
        completed(b"caf\xc3\xa9\r\n", b"\xff bad\n"),
        {"stdout": ["café"], "stderr": ["\\xff bad"], "returncode": 0},
    )


def test_a_stream_the_process_did_not_capture_is_left_out():
    expect.output(completed("x\n", None, 1), {"stdout": ["x"], "returncode": 1})


def test_a_final_newline_ends_the_last_line_and_inner_blank_lines_stay():
    expect.output(completed("a\n\nb"), {"stdout": ["a", "", "b"], "stderr": [], "returncode": 0})


def test_ignore_leaves_a_whole_stream_out(capsys: pytest.CaptureFixture[str]):
    print("noise", file=sys.stderr)
    expect.output(capsys, {"stdout": []}, ignore=['value["stderr"]'])


def test_anything_else_is_refused_with_what_to_do_instead():
    with pytest.raises(TypeError, match=r"got str\. To expect other text, pass its lines"):
        expect.output("text")
