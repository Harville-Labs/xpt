import textwrap
from pathlib import Path

import pytest
from cli_support import xpt
from test_group import seen
from test_record import finished, observation

from xpt import expect
from xpt.expect import ExpectationFailed
from xpt.group import group
from xpt.path import Key
from xpt.record import load_run, to_record
from xpt.render import group_diff
from xpt.value import Dict, Int, Str, to_json


def cases(*pairs: tuple[int, int]) -> Dict:
    return Dict(tuple((Int(k), Int(v)) for k, v in pairs))


def failure(actual: object, expected: object, **how: object) -> str:
    with pytest.raises(AssertionError) as info:
        expect(actual, expected, **how)  # type: ignore
    return str(info.value)


def test_a_keyed_call_compares_against_its_own_case():
    expect(4, {1: 2, 2: 4}, key=2)


def test_a_keyed_mismatch_names_the_case_in_its_path():
    assert failure(5, {1: 2, 2: 4}, key=2) == textwrap.dedent("""\
        expect() mismatch:
          value[2]: 4 -> 5
          1 changed, 0 unchanged""")


def test_a_case_missing_from_the_dict_reports_its_key():
    message = failure(6, {1: 2}, key=3)
    assert message == "expect() has no expected value for key=3. Actual: 6"


def test_a_keyed_expected_value_must_be_a_dict():
    with pytest.raises(TypeError, match=r"with key=, expected must be a dict .* Got list."):
        expect(2, [2], key=1)


def test_ignore_paths_apply_inside_each_case():
    expect({"id": 9, "v": 1}, {"a": {"v": 1}}, key="a", ignore=['value["id"]'])


def test_the_record_holds_the_whole_dict_with_this_case_replaced(recorder):
    with pytest.raises(ExpectationFailed) as info:
        expect(5, {1: 2, 2: 4}, key=2)
    o = info.value.observation
    assert (o.key, o.expected, o.actual) == (Int(2), cases((1, 2), (2, 4)), cases((1, 2), (2, 5)))


def test_failing_cases_merge_into_one_group_and_passing_cases_stay():
    base = cases((1, 2), (2, 4), (3, 6))
    [g] = group(
        [
            seen("t.py::t[2]", key=Int(2), expected=base, actual=cases((1, 2), (2, 5), (3, 6))),
            seen("t.py::t[3]", key=Int(3), expected=base, actual=cases((1, 2), (2, 4), (3, 7))),
        ]
    )
    assert (g.status, g.expected, g.actuals) == ("mismatch", base, (cases((1, 2), (2, 5), (3, 7)),))
    found = group_diff(g)
    assert found is not None
    assert [(c.path, c.actual) for c in found.changes] == [
        ((Key(Int(2)),), Int(5)),
        ((Key(Int(3)),), Int(7)),
    ]


def test_new_cases_alone_group_as_missing_so_expect_create_fills_them():
    [g] = group(
        [
            seen("t.py::t[1]", status="missing", key=Int(1), expected=None, actual=cases((1, 2))),
            seen("t.py::t[2]", status="missing", key=Int(2), expected=None, actual=cases((2, 4))),
        ]
    )
    assert (g.status, g.actuals) == ("missing", (cases((1, 2), (2, 4)),))


def test_a_new_case_beside_existing_ones_is_missing_and_shows_as_an_added_entry():
    base = cases((1, 2))
    new = seen(
        "t.py::t[2]", status="missing", key=Int(2), expected=base, actual=cases((1, 2), (2, 4))
    )
    [g] = group([new])
    assert (g.status, g.actuals) == ("missing", (cases((1, 2), (2, 4)),))
    found = group_diff(g)
    assert found is not None
    assert [(c.op, c.path) for c in found.changes] == [("add", (Key(Int(2)),))]


def test_one_case_with_two_actual_values_is_a_conflict():
    [g] = group(
        [
            seen("t.py::a", key=Int(1), expected=None, actual=cases((1, 2))),
            seen("t.py::b", key=Int(1), expected=None, actual=cases((1, 3))),
        ]
    )
    assert g.status == "conflict"
    assert g.detail.startswith("calls disagree on the actual value for key=1. t.py::a got 2.")


def test_mixing_keyed_and_unkeyed_calls_at_one_site_is_a_conflict():
    [g] = group([seen("t.py::a", key=Int(1)), seen("t.py::b")])
    assert (g.status, g.detail) == ("conflict", "calls disagree on key=. t.py::b passed no key.")


def test_the_key_is_recorded_and_read_back(tmp_path: Path):
    run = "20260101T000000-aaaa"
    keyed = observation(run, key=Str("case"))
    finished(tmp_path, run, keyed)
    [loaded] = load_run(tmp_path)
    assert loaded == keyed
    assert to_record(keyed)["key"] == to_json(Str("case"))
    assert "key" not in to_record(observation(run))


def test_expect_create_fills_every_case_and_the_rerun_passes(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)
    pytester.makepyfile(
        test_sample="""\
        import pytest
        from xpt import expect

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_double(x):
            expect(x * 2, key=x)
        """
    )
    pytester.makeini("[pytest]\n")
    args = ("-p", "no:cacheprovider")
    pytester.runpytest_subprocess(*args, "--expect-create").assert_outcomes(failed=3)
    text = (pytester.path / "test_sample.py").read_text(encoding="utf-8")
    assert "expect(x * 2, {1: 2, 2: 4, 3: 6}, key=x)" in text
    pytester.runpytest_subprocess(*args).assert_outcomes(passed=3)


def test_accept_writes_the_corrected_case_into_the_dict_and_keeps_key(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)
    pytester.makepyfile(
        test_sample="""\
        import pytest
        from xpt import expect

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_double(x):
            expect(x * 2, {1: 2, 2: 5, 3: 6}, key=x)
        """
    )
    pytester.makeini("[pytest]\n")
    args = ("-p", "no:cacheprovider")
    pytester.runpytest_subprocess(*args).assert_outcomes(passed=2, failed=1)
    assert xpt(capsys, "accept", "--all").code == 0
    text = (pytester.path / "test_sample.py").read_text(encoding="utf-8")
    assert "expect(x * 2, {1: 2, 2: 4, 3: 6}, key=x)" in text
    pytester.runpytest_subprocess(*args).assert_outcomes(passed=3)
