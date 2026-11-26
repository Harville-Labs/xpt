import json
from pathlib import Path

import pytest
from cli_support import MISMATCH, observed, recorded, xpt

from xpt.value import Str

TABLE = "".join(
    f"| {name} | 0.{score} |\n" for name, score in zip("ABCDEFGH", range(80, 88), strict=True)
)
CHANGED = TABLE.replace("| D | 0.83 |", "| D | 0.93 |")

LINES = [
    "@@ -3,3 +3,3 @@",
    " | C | 0.82 |",
    "-| D | 0.83 |",
    "+| D | 0.93 |",
    " | E | 0.84 |",
]

SOURCE = f"""\
from xpt import expect

def test_table():
    expect({CHANGED!r}, {TABLE!r})

def test_assert():
    assert {TABLE!r} == {CHANGED!r}
"""


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    pytester.makepyfile(test_table=SOURCE)
    pytester.makeini("[pytest]\n")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)


def block(result: pytest.RunResult, test: str) -> list[str]:
    out = result.stdout.lines
    start = next(
        i for i, line in enumerate(out) if line.startswith(f"FAILED test_table.py::{test}")
    )
    end = next(i for i in range(start + 1, len(out)) if not out[i].startswith("  "))
    return out[start + 2 : end]


def test_a_multi_line_string_mismatch_reports_the_changed_lines(pytester: pytest.Pytester):
    result = run(pytester)
    [id, accept] = [block(result, "test_table")[0].split()[0], block(result, "test_table")[-1]]
    assert block(result, "test_table") == [
        f"  {id}  mismatch",
        "  value: lines differ, - expected + actual",
        *(f"    {line}" for line in LINES),
        "  1 changed, 0 unchanged",
        accept,
    ]


def test_a_compared_assert_on_multi_line_strings_labels_the_lines_left_and_right(
    pytester: pytest.Pytester,
):
    assert block(run(pytester), "test_assert") == [
        "  value: lines differ, - left + right",
        *(f"    {line}" for line in LINES),
        "  1 changed, 0 unchanged",
    ]


def test_xpt_json_carries_the_line_diff(pytester: pytest.Pytester):
    run(pytester, "--xpt-json=report.json")
    data = json.loads((pytester.path / "report.json").read_text(encoding="utf-8"))
    expect_failure, assert_failure = data["failures"]
    [change] = expect_failure["group"]["changes"]
    assert change["lines"] == LINES
    [change] = assert_failure["changes"]
    assert change["lines"] == LINES


def test_xpt_failures_and_json_show_the_line_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(tmp_path)
    recorded(tmp_path, [observed("tests/test_a.py::test_a", MISMATCH, Str(CHANGED), Str(TABLE))])
    text = xpt(capsys, "failures").out.splitlines()
    assert text[1:-1] == [
        "  value: lines differ, - expected + actual",
        *(f"    {line}" for line in LINES),
        "  1 changed, 0 unchanged",
    ]
    [g] = json.loads(xpt(capsys, "failures", "--json").out)["groups"]
    assert g["changes"][0]["lines"] == LINES


def test_a_long_line_diff_is_cut_to_the_limit_and_verbose_shows_more(pytester: pytest.Pytester):
    many = "".join(f"line {i}\n" for i in range(40))
    pytester.makepyfile(
        test_table=f"from xpt import expect\n\ndef test_table():\n"
        f"    expect({many.replace('line', 'LINE')!r}, {many!r})\n"
    )
    pytester.makeini("[pytest]\n")
    quiet = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert "    ... 61 more lines" in quiet.stdout.lines
    verbose = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-v")
    assert "    ... 61 more lines" not in verbose.stdout.lines
    assert "    +LINE 39" in verbose.stdout.lines


def test_a_single_line_string_mismatch_keeps_the_one_line_form(pytester: pytest.Pytester):
    pytester.makepyfile(
        test_table='from xpt import expect\n\ndef test_table():\n    expect("b", "a")\n'
    )
    pytester.makeini("[pytest]\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert '  value: "a" -> "b"' in result.stdout.lines
