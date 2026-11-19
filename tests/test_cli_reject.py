import json
from pathlib import Path

import pytest
from cli_support import EVERY_KIND, MISMATCH, MISSING, RUN, Ran, observed, op, recorded, xpt

from xpt.value import Str

DECISIONS = Path(".xpt") / "decisions.jsonl"
LATER = "20260102T000000-cccc"
LAST = "20260103T000000-dddd"


@pytest.fixture(autouse=True)
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def decisions(project: Path) -> list[dict[str, object]]:
    text = (project / DECISIONS).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_reject_records_the_call_site_and_the_rejected_value(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "reject", "df55")
    assert ran == Ran(
        0,
        "df55  tests/test_a.py:7  rejected\n"
        "Recorded 1 rejection in .xpt/decisions.jsonl (1 rejected). "
        "Next: fix the code under test, then run the tests.\n",
        "",
    )
    [record] = decisions(project)
    assert record == {
        "schema_version": 1,
        "decision": "reject",
        "run": RUN,
        "site": {"path": "tests/test_a.py", "qualname": "test_a", "callee": "expect", "ordinal": 0},
        "actual_sha": record["actual_sha"],
    }
    assert isinstance(record["actual_sha"], str)
    assert len(record["actual_sha"]) == 64


def test_reject_json_lists_each_outcome(project: Path, capsys: pytest.CaptureFixture[str]):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "reject", "aa26", "--json")
    assert ran.code == 0
    assert ran.json == {
        "schema_version": 1,
        "command": "reject",
        "run": RUN,
        "outcomes": [
            {
                "id": "aa26",
                "site": {"path": "tests/test_a.py", "line": 12, "qualname": "test_b"},
                "status": "rejected",
                "reason": "",
            }
        ],
    }


def test_rejecting_twice_records_once(project: Path, capsys: pytest.CaptureFixture[str]):
    recorded(project, EVERY_KIND)
    xpt(capsys, "reject", "df55")
    ran = xpt(capsys, "reject", "df55", "df55")
    assert ran == Ran(
        0,
        "df55  tests/test_a.py:7  already_rejected\n"
        "Nothing new to record (1 already_rejected). "
        "Next: fix the code under test, then run the tests.\n",
        "",
    )
    assert len(decisions(project)) == 1


def test_a_group_without_one_actual_value_is_skipped_and_exits_one(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "reject", "c9cc", "df55")
    assert ran.code == 1
    assert ran.out.splitlines() == [
        "c9cc  tests/test_b.py:3  skipped",
        "  calls disagree on the actual value. tests/test_b.py::test_c[1] got 1. "
        "tests/test_b.py::test_c[2] got 2. Give each case its own expect().",
        "df55  tests/test_a.py:7  rejected",
        "Recorded 1 rejection in .xpt/decisions.jsonl (1 skipped, 1 rejected). "
        "Next: fix the code under test, then run the tests.",
    ]


def test_failures_marks_a_rejected_group_and_stops_offering_it(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    xpt(capsys, "reject", "df55")
    ran = xpt(capsys, "failures")
    assert ran.code == 1
    lines = ran.out.splitlines()
    assert lines[0] == "df55  tests/test_a.py:7  mismatch  rejected  2 tests"
    assert lines[-1] == (
        f"4 failures in run {RUN} (1 mismatch, 1 missing, 1 conflict, 1 unlocatable), "
        "1 acceptable, 1 rejected. Next: xpt accept aa26"
    )
    groups = xpt(capsys, "failures", "--json").json["groups"]
    assert [g["rejected"] for g in groups] == [True, False, False, False]


def test_failures_with_every_acceptable_group_rejected_suggests_no_accept(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, [observed("t::a", MISMATCH, op("sub"), op("add"))])
    xpt(capsys, "reject", "df55")
    assert xpt(capsys, "failures").out.splitlines()[-1] == (
        f"1 failure in run {RUN} (1 mismatch), 0 acceptable, 1 rejected. "
        "Fix the tests and run them again."
    )


def test_a_rejection_matches_only_the_rejected_value_at_that_call_site(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, [observed("t::a", MISMATCH, op("sub"), op("add"))])
    xpt(capsys, "reject", "df55")
    recorded(project, [observed("t::a", MISMATCH, op("mul"), op("add"))], run=LATER)
    assert xpt(capsys, "failures").out.splitlines() == [
        "df55  tests/test_a.py:7  mismatch  1 test",
        '  value["op"]: "add" -> "mul"',
        "  1 changed, 1 unchanged",
        f"1 failure in run {LATER} (1 mismatch), 1 acceptable. Next: xpt accept df55",
    ]
    recorded(project, [observed("t::a", MISMATCH, op("sub"), op("add"))], run=LAST)
    ran = xpt(capsys, "failures")
    assert ran.out.splitlines()[0] == "df55  tests/test_a.py:7  mismatch  rejected  1 test"


def test_show_says_a_rejected_group_can_still_be_accepted_by_id(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    xpt(capsys, "reject", "df55")
    assert xpt(capsys, "show", "df55").out.splitlines()[-1] == (
        "df55 was rejected as a bug. Fix the code under test and run the tests again, "
        "or run xpt accept df55 to accept it anyway."
    )


def test_bulk_accept_leaves_rejected_groups_alone(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, [observed("t::b", MISSING, Str("x"))])
    xpt(capsys, "reject", "aa26")
    for flag in ("--all", "--missing"):
        assert xpt(capsys, "accept", flag) == Ran(0, f"Nothing to accept in run {RUN}.\n", "")


def test_reject_needs_at_least_one_id(capsys: pytest.CaptureFixture[str]):
    ran = xpt(capsys, "reject")
    assert (ran.code, ran.out) == (2, "")
    assert ran.err.startswith("xpt: error: the following arguments are required: id. ")


def test_an_unknown_id_is_a_usage_error_and_records_nothing(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    ran = xpt(capsys, "reject", "df55", "beef")
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == f"xpt: error: no group beef in run {RUN}. Run xpt failures to list the ids.\n"
    assert not (project / DECISIONS).exists()


def test_a_held_lock_is_a_usage_error_and_records_nothing(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    (project / ".xpt" / "accept.lock").write_text("1", encoding="utf-8")
    ran = xpt(capsys, "reject", "df55")
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == (
        "xpt: error: .xpt/accept.lock exists, so another xpt command is writing to .xpt/. "
        "If none is, delete .xpt/accept.lock and run the command again.\n"
    )
    assert not (project / DECISIONS).exists()


def test_a_malformed_decision_is_reported_with_its_file_and_line(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    recorded(project, EVERY_KIND)
    xpt(capsys, "reject", "df55")
    with (project / DECISIONS).open("a", encoding="utf-8") as file:
        file.write('{"schema_version": 1, "decision": "accept"}\n')
    ran = xpt(capsys, "failures")
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == (
        "xpt: error: .xpt/decisions.jsonl:2: decision must be \"reject\", got 'accept'.\n"
    )
