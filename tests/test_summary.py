from typing import Any, cast

import pytest

from xpt.decisions import Rejection, rejection
from xpt.failure import (
    Compared,
    Detail,
    Expected,
    Failure,
    Frame,
    Raised,
    Text,
    Where,
    streams,
    where_from_json,
    where_json,
)
from xpt.group import group
from xpt.record import Observation, Status
from xpt.site import CallSite
from xpt.summary import Limits, Session, limits, report_json, report_lines
from xpt.value import Dict, Int, Str, Value

QUIET = limits(0)
SITE = CallSite("t.py", "test_a", "expect", 0, "ab" * 32, 3)


def failure(test: str = "t.py::test_a", detail: Detail | None = None, **changes: object) -> Failure:
    fields: dict[str, object] = {
        "test": test,
        "when": "call",
        "where": Where("t.py", 3, None, detail or Raised("ValueError", "bad")),
        "output": (),
        "traceback": "Traceback text",
    }
    fields.update(changes)
    return Failure(**fields)  # type: ignore[arg-type]


def session(
    *failures: Failure, run: str | None = None, rejected: frozenset[Rejection] = frozenset()
) -> Session:
    return Session(failures, ("1 failed",), 0.5, 1, run, rejected)


ONE = Int(1)


def observed(status: Status = "mismatch", expected: Value | None = ONE) -> Observation:
    return Observation("r", "t.py::test_a", SITE, status, expected, Int(2), "")


def test_verbosity_raises_the_limits_and_very_verbose_lifts_them():
    assert limits(-1) == limits(0) == Limits(10, 80, 5, 200, traceback=False)
    assert limits(1) == Limits(50, 400, 20, 1000, traceback=False)
    assert limits(2) == Limits(None, None, None, None, traceback=True)


def test_parametrized_cases_failing_alike_fold_into_the_first_by_full_node_id():
    cases = [failure(f"t.py::test_a[{i}]") for i in range(3)]
    assert report_lines(session(*cases), QUIET) == [
        "FAILED t.py::test_a[0]  t.py:3",
        "  ValueError: bad",
        "  also fails: t.py::test_a[1]",
        "  also fails: t.py::test_a[2]",
        "1 failed in 0.50s.",
    ]


def test_a_folded_case_with_a_different_message_shows_its_first_differing_line():
    cases = [
        failure(f"t.py::test_a[{i}]", Raised("AssertionError", f"assert {i} == 0\nx"))
        for i in (1, 2)
    ]
    assert report_lines(session(*cases), QUIET)[1:-1] == [
        "  AssertionError: assert 1 == 0",
        "  x",
        "  also fails: t.py::test_a[2]  assert 2 == 0",
    ]


def test_folded_cases_beyond_the_limit_are_counted():
    cases = [failure(f"t.py::test_a[{i}]") for i in range(4)]
    lim = Limits(2, 80, 5, 200, traceback=False)
    assert report_lines(session(*cases), lim)[2:-1] == [
        "  also fails: t.py::test_a[1]",
        "  also fails: t.py::test_a[2]",
        "  ... 1 more case",
    ]


def test_cases_failing_with_different_exceptions_stay_apart():
    first = failure("t.py::test_a[0]")
    second = failure("t.py::test_a[1]", Raised("KeyError", "'k'"))
    assert report_lines(session(first, second), QUIET)[:4] == [
        "FAILED t.py::test_a[0]  t.py:3",
        "  ValueError: bad",
        "FAILED t.py::test_a[1]  t.py:3",
        "  KeyError: 'k'",
    ]


def test_an_exception_message_becomes_one_cut_line():
    long = failure(detail=Raised("ValueError", "first\n  second " + "x" * 300))
    [_, line, _] = report_lines(session(long), QUIET)
    assert line == f"  ValueError: first second {'x' * 175}... (325 chars)"


def test_an_assertion_keeps_its_explanation_lines_up_to_the_limit():
    message = "\n".join(["assert 1 == 2", *(f"line {i}" for i in range(12))])
    lines = report_lines(session(failure(detail=Raised("AssertionError", message))), QUIET)
    assert lines[1:4] == ["  AssertionError: assert 1 == 2", "  line 0", "  line 1"]
    assert lines[-2:] == ["  ... 3 more lines", "1 failed in 0.50s."]


def test_text_detail_prefers_pytest_error_lines():
    text = "def test():\n>   import nope\nE   ModuleNotFoundError: nope\n"
    lines = report_lines(session(failure(detail=Text(text))), QUIET)
    assert lines[1:-1] == ["  E   ModuleNotFoundError: nope"]


def test_a_compared_assert_labels_each_side_instead_of_an_arrow():
    left = Dict(((Str("a"), Int(1)), (Str("b"), Int(3))))
    right = Dict(((Str("a"), Int(2)), (Str("c"), Int(4))))
    assert report_lines(session(failure(detail=Compared(left, right))), QUIET)[1:-1] == [
        '  value["a"]: left 1, right 2',
        '  value["b"]: left 3, right missing',
        '  value["c"]: left missing, right 4',
        "  3 changed, 0 unchanged",
    ]


def test_the_block_and_footer_name_the_accept_command_only_when_the_run_was_recorded():
    lines = report_lines(session(failure(detail=Expected(observed())), run="r"), QUIET)
    assert lines[1:] == [
        "  3b78  mismatch",
        "  value: 1 -> 2",
        "  1 changed, 0 unchanged",
        "  accept: xpt accept 3b78",
        "1 failed in 0.50s. Next: xpt accept 3b78",
    ]
    unrecorded = report_lines(session(failure(detail=Expected(observed()))), QUIET)
    assert unrecorded[-2:] == ["  1 changed, 0 unchanged", "1 failed in 0.50s."]


def test_the_footer_points_at_xpt_failures_when_nothing_is_acceptable():
    unsupported = observed("unsupported", None)
    lines = report_lines(session(failure(detail=Expected(unsupported)), run="r"), QUIET)
    assert lines[-1] == "1 failed in 0.50s. Next: xpt failures"


def test_a_rejected_group_is_marked_and_left_out_of_the_footer():
    failed = failure(detail=Expected(observed()))
    [g] = group([observed()])
    found = rejection(g)
    assert found is not None
    lines = report_lines(session(failed, run="r", rejected=frozenset([found])), QUIET)
    assert lines[1] == "  3b78  mismatch  rejected"
    assert lines[-1] == "1 failed in 0.50s. Next: xpt failures"
    data = report_json(session(failed, run="r", rejected=frozenset([found])))
    [entry] = cast(list[dict[str, Any]], data["failures"])
    assert (entry["group"]["rejected"], data["summary"]) == (True, lines[-1])


def test_output_shows_the_last_lines_of_each_stream_and_very_verbose_the_traceback():
    output = streams(
        [
            ("Captured stdout setup", "a\n"),
            ("Captured stdout call", "b\nc\n"),
            ("Captured log call", "WARNING x\n"),
            ("Captured stderr call", "   \n"),
            ("some other section", "ignored"),
        ]
    )
    assert output == (("stdout", "a\nb\nc\n"), ("log", "WARNING x\n"))
    f = failure(output=output)
    assert report_lines(session(f), Limits(10, 80, 2, 200, traceback=True))[2:-1] == [
        "  stdout: b",
        "  stdout: c",
        "  log: WARNING x",
        "  traceback:",
        "    Traceback text",
    ]


def test_the_header_shows_the_call_chain_from_the_test_down():
    callers = (Frame("t.py", 9, None), Frame("t.py", 5, "outer"))
    f = failure(where=Where("t.py", 2, "inner", Raised("ValueError", "bad"), callers))
    assert report_lines(session(f), QUIET)[0] == (
        "FAILED t.py::test_a  t.py:9 > t.py:5 outer > t.py:2 inner"
    )


def test_a_long_call_chain_keeps_the_test_and_the_frames_nearest_the_error():
    callers = tuple(Frame("t.py", i, f"f{i}") for i in range(10, 16))
    f = failure(where=Where("t.py", 2, "inner", Raised("ValueError", "bad"), callers))
    assert report_lines(session(f), QUIET)[0] == (
        "FAILED t.py::test_a  t.py:10 f10 > ... > t.py:14 f14 > t.py:15 f15 > t.py:2 inner"
    )


def test_the_failing_source_line_is_shown_and_cut():
    where = Where("t.py", 3, None, Raised("ValueError", "bad"), source="x = " + "1" * 100)
    assert report_lines(session(failure(where=where)), QUIET)[1:3] == [
        f"  > x = {'1' * 76}... (104 chars)",
        "  ValueError: bad",
    ]


def test_a_chained_cause_follows_the_error_on_one_cut_line():
    where = Where("t.py", 3, None, Raised("ValueError", "bad"), cause="KeyError: " + "k" * 300)
    assert report_lines(session(failure(where=where)), QUIET)[1:3] == [
        "  ValueError: bad",
        f"  caused by: KeyError: {'k' * 190}... (310 chars)",
    ]


def test_a_collection_error_header_does_not_repeat_the_path():
    f = failure("t.py", when="collect", where=Where("t.py", None, None, Text("E   boom")))
    assert report_lines(session(f), QUIET)[0] == "ERROR t.py (collect)"


@pytest.mark.parametrize(
    "detail",
    [
        Expected(observed()),
        Compared(Str("a"), Str("b")),
        Raised("ValueError", "bad"),
        Text("E   boom"),
    ],
    ids=["expect", "compare", "raised", "text"],
)
def test_where_survives_its_json_round_trip(detail: Detail):
    where = Where(
        "t.py", 3, "helper", detail, (Frame("t.py", 9, None),), "helper()", "KeyError: 'k'"
    )
    assert where_from_json(where_json(where)) == where


def test_where_from_json_rejects_an_unknown_detail_kind():
    with pytest.raises(ValueError, match=r'detail\.kind must be "expect"'):
        where_from_json(
            {"path": "t.py", "line": 1, "function": None, "callers": [], "detail": {"kind": "?"}}
        )


def test_the_json_report_keeps_values_uncut():
    detail = Compared(Str("a" * 300), Str("b"))
    data = report_json(session(failure(detail=detail)))
    [entry] = cast(list[dict[str, object]], data["failures"])
    assert entry["changes"] == [{"path": "value", "op": "replace", "left": "a" * 300, "right": "b"}]


def test_a_compared_assert_names_its_json_sides_left_and_right():
    detail = Compared(Dict(((Str("a"), Int(1)),)), Dict(((Str("b"), Int(2)),)))
    data = report_json(session(failure(detail=detail)))
    [entry] = cast(list[dict[str, object]], data["failures"])
    assert entry["changes"] == [
        {"path": 'value["a"]', "op": "remove", "left": 1},
        {"path": 'value["b"]', "op": "add", "right": 2},
    ]
