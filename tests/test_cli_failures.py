from pathlib import Path

import pytest
from cli_support import EVERY_KIND, MISMATCH, MISSING, OLD, RUN, Ran, observed, op, recorded, xpt

from xpt.record import state_directory
from xpt.value import Dict, Int, Null, Str

CONFLICT_DETAIL = (
    "calls disagree on the actual value. tests/test_b.py::test_c[1] got 1. "
    "tests/test_b.py::test_c[2] got 2. Give each case its own expect()."
)
LOST_DETAIL = "expect() was called from code with no source file."


@pytest.fixture(autouse=True)
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_failures_prints_each_group_with_its_diff_and_a_footer_naming_the_next_command(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "failures")
    assert (ran.code, ran.err) == (1, "")
    assert ran.out == (
        "df55  tests/test_a.py:7  mismatch  2 tests\n"
        '  value["op"]: "add" -> "sub"\n'
        "  1 changed, 1 unchanged\n"
        "aa26  tests/test_a.py:12  missing  1 test\n"
        '  actual: "héllo"\n'
        "c9cc  tests/test_b.py:3  conflict  2 tests\n"
        f"  {CONFLICT_DETAIL}\n"
        "-  tests/test_c.py:5  unlocatable  1 test\n"
        f"  {LOST_DETAIL}\n"
        f"4 failures in run {RUN} (1 mismatch, 1 missing, 1 conflict, 1 unlocatable), "
        "2 acceptable. Next: xpt accept df55 aa26\n"
    )


def test_failures_json_carries_every_group_with_values_and_changes(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "failures", "--json")
    assert (ran.code, ran.err) == (1, "")
    assert ran.out.count("\n") == 1
    assert ran.json == {
        "schema_version": 1,
        "command": "failures",
        "run": RUN,
        "groups": [
            {
                "id": "df55",
                "site": {"path": "tests/test_a.py", "line": 7, "qualname": "test_a"},
                "status": "mismatch",
                "acceptable": True,
                "tests": ["tests/test_a.py::test_a[1]", "tests/test_a.py::test_a[2]"],
                "detail": "",
                "actuals": [{"dict": {"op": "sub", "n": 1}}],
                "expected": {"dict": {"op": "add", "n": 1}},
                "changes": [
                    {"path": 'value["op"]', "op": "replace", "expected": "add", "actual": "sub"}
                ],
                "unchanged": 1,
            },
            {
                "id": "aa26",
                "site": {"path": "tests/test_a.py", "line": 12, "qualname": "test_b"},
                "status": "missing",
                "acceptable": True,
                "tests": ["tests/test_a.py::test_b"],
                "detail": "",
                "actuals": ["héllo"],
            },
            {
                "id": "c9cc",
                "site": {"path": "tests/test_b.py", "line": 3, "qualname": "test_c"},
                "status": "conflict",
                "acceptable": False,
                "tests": ["tests/test_b.py::test_c[1]", "tests/test_b.py::test_c[2]"],
                "detail": CONFLICT_DETAIL,
                "actuals": [1, 2],
            },
            {
                "id": None,
                "site": {"path": "tests/test_c.py", "line": 5, "qualname": None},
                "status": "unlocatable",
                "acceptable": False,
                "tests": ["tests/test_c.py::test_d"],
                "detail": LOST_DETAIL,
                "actuals": [3],
            },
        ],
    }


def test_a_recorded_none_is_an_expected_value_and_not_an_absent_one(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, [observed("t::a", MISMATCH, Int(1), Null())])
    [g] = xpt(capsys, "failures", "--json").json["groups"]
    assert (g["expected"], g["actuals"]) == (None, [1])


def test_a_clean_latest_run_has_no_failures_and_exits_zero(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    state_directory(project)
    recorded(project, [])
    assert xpt(capsys, "failures") == Ran(0, f"No failures in run {RUN}.\n", "")
    ran = xpt(capsys, "failures", "--json")
    assert (ran.code, ran.json) == (
        0,
        {"schema_version": 1, "command": "failures", "run": RUN, "groups": []},
    )


@pytest.mark.parametrize("command", ["failures", "show df55", "accept df55", "accept --all"])
def test_a_project_with_nothing_recorded_is_a_usage_error(
    command: str, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, *command.split())
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == "xpt: error: .xpt/latest does not exist. Run the tests to record a run.\n"


def test_run_selects_an_older_run_and_the_next_command_keeps_it(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, [observed("t::b", MISSING, Int(5))], run=OLD)
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "failures", "--run", OLD)
    assert (ran.code, ran.err) == (1, "")
    assert ran.out == (
        "aa26  tests/test_a.py:12  missing  1 test\n"
        "  actual: 5\n"
        f"1 failure in run {OLD} (1 missing), 1 acceptable. Next: xpt accept --run {OLD} aa26\n"
    )


def test_an_unknown_run_is_a_usage_error(project: Path, capsys: pytest.CaptureFixture[str]):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "failures", "--run", OLD)
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == f"xpt: error: there is no run {OLD} in .xpt/runs.\n"


def test_a_malformed_record_is_reported_with_its_file_and_line(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    (project / ".xpt" / "runs" / RUN / "main.jsonl").write_text("{\n", encoding="utf-8")
    ran = xpt(capsys, "failures")
    assert ran.code == 2
    assert ran.err.startswith(f"xpt: error: .xpt/runs/{RUN}/main.jsonl:1: not JSON")
    assert ran.err.count("\n") == 1


def test_show_prints_one_group_with_its_function_tests_and_diff(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "show", "df55")
    assert (ran.code, ran.err) == (0, "")
    assert ran.out == (
        "df55  tests/test_a.py:7  mismatch  2 tests\n"
        "  function: test_a\n"
        "  test: tests/test_a.py::test_a[1]\n"
        "  test: tests/test_a.py::test_a[2]\n"
        '  value["op"]: "add" -> "sub"\n'
        "  1 changed, 1 unchanged\n"
        "Next: xpt accept df55\n"
    )


def test_show_json_is_the_group_alone(project: Path, capsys: pytest.CaptureFixture[str]):
    recorded(project, EVERY_KIND)
    whole = xpt(capsys, "failures", "--json").json
    ran = xpt(capsys, "show", "aa26", "--json")
    assert ran.code == 0
    assert ran.json == {
        "schema_version": 1,
        "command": "show",
        "run": RUN,
        "group": whole["groups"][1],
    }


def test_show_says_why_a_conflict_cannot_be_accepted(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "show", "c9cc")
    assert ran.code == 0
    assert ran.out.endswith(
        f"  {CONFLICT_DETAIL}\nc9cc cannot be accepted. Fix the test and run it again.\n"
    )


def test_an_unknown_id_is_a_usage_error(project: Path, capsys: pytest.CaptureFixture[str]):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "show", "beef")
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == (
        f"xpt: error: no group beef in run {RUN}. Run xpt failures to list the ids.\n"
    )


def big(n: int) -> Dict:
    return Dict(tuple((Str(f"k{i}"), Int(i * n)) for i in range(12)))


def test_long_values_and_long_diffs_are_cut_unless_full(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    long = "x" * 100
    recorded(
        project,
        [observed("t::a", MISMATCH, big(2), big(1)), observed("t::b", MISSING, Str(long))],
    )
    cut = xpt(capsys, "failures").out.splitlines()
    assert cut[1:3] == ['  value["k1"]: 1 -> 2', '  value["k2"]: 2 -> 4']
    assert cut[10:13] == [
        '  value["k10"]: 10 -> 20',
        "  ... 1 more changes",
        "  11 changed, 1 unchanged",
    ]
    assert cut[14] == f'  actual: "{long[:79]}... (102 chars)'

    full = xpt(capsys, "show", "df55", "--full").out.splitlines()
    assert full[3].startswith('  expected: {"k0": 0, "k1": 1, ')
    assert full[4].startswith('  actual: {"k0": 0, "k1": 2, ')
    assert len(full[4]) > 100
    assert full[-3:] == ['  value["k11"]: 11 -> 22', "  11 changed, 1 unchanged", full[-1]]
    assert xpt(capsys, "show", "aa26", "--full").out.splitlines()[3] == f'  actual: "{long}"'


def test_runs_lists_runs_newest_first_and_marks_the_latest(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, [observed("t::b", MISSING, Int(5))], run=OLD)
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "runs")
    assert (ran.code, ran.err) == (0, "")
    assert ran.out == (
        f"{RUN}  4 failures  latest\n{OLD}  1 failure\n2 runs. Next: xpt failures --run ID\n"
    )
    assert xpt(capsys, "runs", "--json").json == {
        "schema_version": 1,
        "command": "runs",
        "runs": [
            {"id": RUN, "latest": True, "failures": 4},
            {"id": OLD, "latest": False, "failures": 1},
        ],
    }


def test_runs_with_nothing_recorded_says_how_to_record(capsys: pytest.CaptureFixture[str]):
    ran = xpt(capsys, "runs")
    assert ran == Ran(0, "No runs under .xpt/. Run the tests to record one.\n", "")


def test_root_points_at_another_project(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    recorded(project, [observed("t::a", MISMATCH, op("sub"), op("add"))])
    elsewhere = project / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert xpt(capsys, "failures", "--root", str(project)).code == 1
    assert xpt(capsys, "failures").code == 2
