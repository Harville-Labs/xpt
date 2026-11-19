import pytest
from hypothesis import given
from hypothesis import strategies as st

from xpt.group import Group, group
from xpt.record import Observation, load_run
from xpt.site import CallSite, Unlocatable
from xpt.value import Int, Str

SHA = "ab" * 32
SITE = CallSite("tests/test_a.py", "test_a", 0, SHA, 7)


def seen(test: str = "tests/test_a.py::test_a", **changes: object) -> Observation:
    fields: dict[str, object] = {
        "run": "20260101T000000-aaaa",
        "test": test,
        "site": SITE,
        "status": "mismatch",
        "expected": Int(1),
        "actual": Int(2),
        "detail": "",
    }
    fields.update(changes)
    return Observation(**fields)  # type: ignore[arg-type]


def test_one_failing_call_makes_one_acceptable_group():
    [g] = group([seen()])
    assert g == Group(
        id=g.id,
        site=SITE,
        status="mismatch",
        actuals=(Int(2),),
        expected=Int(1),
        tests=("tests/test_a.py::test_a",),
        detail="",
    )
    assert g.acceptable
    assert g.id is not None


def with_sha(sha: str) -> CallSite:
    return CallSite(SITE.path, SITE.qualname, SITE.ordinal, sha, SITE.line)


def test_a_missing_expectation_groups_as_missing_and_acceptable():
    [g] = group([seen(status="missing", expected=None, site=with_sha(""))])
    assert (g.status, g.expected, g.acceptable) == ("missing", None, True)


def test_parametrized_cases_with_equal_actuals_collapse_into_one_group_listing_every_test():
    [g] = group([seen("t.py::t[2]"), seen("t.py::t[1]"), seen("t.py::t[1]")])
    assert (g.status, g.actuals, g.tests) == ("mismatch", (Int(2),), ("t.py::t[1]", "t.py::t[2]"))
    assert g.acceptable


def test_differing_actuals_are_a_conflict_that_names_the_tests_behind_each_value():
    [g] = group([seen("t.py::t[1]"), seen("t.py::t[2]", actual=Int(3))])
    assert (g.status, g.actuals, g.acceptable) == ("conflict", (Int(2), Int(3)), False)
    assert g.detail == (
        "calls disagree on the actual value. t.py::t[1] got 2. t.py::t[2] got 3. "
        "Give each case its own expect()."
    )


def test_actuals_that_python_considers_equal_do_not_conflict():
    [g] = group([seen("a", actual=Int(1)), seen("b", actual=Int(1))])
    assert g.status == "mismatch"


def test_a_changed_expected_argument_during_the_run_is_a_conflict():
    other = with_sha("cd" * 32)
    [g] = group([seen("t.py::a"), seen("t.py::b", site=other)])
    assert (g.status, g.acceptable) == ("conflict", False)
    assert g.detail.startswith("calls disagree on the expected argument's source text.")
    assert "t.py::a" in g.detail and "t.py::b" in g.detail


def test_equal_actuals_against_different_expected_values_are_a_conflict():
    [g] = group([seen("t.py::a"), seen("t.py::b", expected=Int(5))])
    assert (g.status, g.expected) == ("conflict", None)
    assert g.detail == (
        "calls disagree on the expected value. t.py::a had 1. t.py::b had 5. "
        "Give each case its own expect()."
    )


def test_observations_from_different_workers_merge_into_one_group():
    gw0 = seen("t.py::t[1]", run="20260101T000000-aaaa")
    gw1 = seen("t.py::t[2]", run="20260101T000000-aaaa")
    [g] = group([gw1, gw0])
    assert g.tests == ("t.py::t[1]", "t.py::t[2]")


def test_an_unsupported_value_groups_as_unsupported_and_is_not_acceptable():
    reason = 'value["f"] is a builtin_function_or_method; xpt cannot record it.'
    [g] = group([seen(status="unsupported", expected=None, actual=None, detail=reason)])
    assert (g.status, g.actuals, g.detail, g.acceptable) == ("unsupported", (), reason, False)


def test_a_value_over_the_size_cap_groups_as_unsupported_and_is_not_acceptable():
    detail = "too large: 2000000 bytes, sha256 " + "0" * 64
    [g] = group([seen(expected=None, actual=None, detail=detail)])
    assert (g.status, g.detail, g.acceptable) == ("unsupported", detail, False)


def test_one_unrecordable_call_makes_the_whole_site_unsupported():
    [g] = group([seen("a"), seen("b", status="unsupported", actual=None, detail="no")])
    assert (g.status, g.actuals, g.tests, g.detail) == ("unsupported", (Int(2),), ("a", "b"), "no")


def test_unlocatable_calls_are_shown_by_place_and_reason_and_never_acceptable():
    lost = Unlocatable("<string> has no source file", "<string>", 3)
    [g] = group([seen("a", site=lost), seen("b", site=lost, actual=Int(9))])
    assert (g.id, g.site, g.status, g.acceptable) == (None, lost, "unlocatable", False)
    assert (g.actuals, g.tests, g.detail) == ((Int(2), Int(9)), ("a", "b"), lost.reason)


def test_unlocatable_calls_with_different_reasons_stay_apart():
    one = Unlocatable("first", "<string>", 3)
    two = Unlocatable("second", "<string>", 3)
    assert len(group([seen(site=one), seen(site=two)])) == 2


def test_short_ids_are_unique_and_at_least_four_characters():
    sites = [CallSite("t.py", f"test_{i}", 0, SHA, i) for i in range(300)]
    ids = [g.id for g in group(seen(site=s) for s in sites)]
    assert len(set(ids)) == 300
    assert all(i is not None and len(i) >= 4 for i in ids)


def test_groups_are_ordered_by_path_then_line_then_ordinal():
    b = CallSite("b.py", "t", 0, SHA, 1)
    a_late = CallSite("a.py", "t", 0, SHA, 20)
    a_early_second = CallSite("a.py", "u", 1, SHA, 5)
    a_early_first = CallSite("a.py", "u", 0, SHA, 5)
    lost = Unlocatable("gone", "a.py", 10)
    found = group(seen(site=s) for s in (b, lost, a_late, a_early_second, a_early_first))
    assert [g.site for g in found] == [a_early_first, a_early_second, lost, a_late, b]


EVERY_CASE = [
    seen("t.py::a"),
    seen("t.py::b"),
    seen("t.py::c", actual=Int(3)),
    seen("t.py::d", site=CallSite("t.py", "test_d", 0, SHA, 30)),
    seen("t.py::d", site=CallSite("t.py", "test_d", 0, "cd" * 32, 31)),
    seen("t.py::e", site=CallSite("u.py", "test_e", 1, SHA, 4), actual=Str("x")),
    seen("t.py::e", site=CallSite("u.py", "test_e", 1, SHA, 4), actual=Str("x")),
    seen("t.py::f", site=Unlocatable("gone", "u.py", 2), actual=None, status="unsupported"),
    seen("t.py::g", site=Unlocatable("gone", "u.py", 2)),
    seen("t.py::h", site=CallSite("v.py", "test_h", 0, SHA, 1), expected=Int(2)),
    seen("t.py::i", site=CallSite("v.py", "test_h", 0, SHA, 1), expected=Int(3)),
]


@given(st.permutations(EVERY_CASE))
def test_grouping_does_not_depend_on_the_order_observations_were_read(shuffled):
    assert group(shuffled) == group(EVERY_CASE)


def test_a_parametrized_failure_across_xdist_workers_loads_as_one_group(
    pytester: pytest.Pytester,
):
    pytester.makepyfile(
        test_param="""\
import pytest
from xpt import expect

@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_n(n):
    expect(n % 1, 1)
"""
    )
    pytester.makeini("[pytest]\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-n", "2")
    result.assert_outcomes(failed=4)
    run_dir = next((pytester.path / ".xpt" / "runs").iterdir())
    assert len(list(run_dir.iterdir())) == 2
    [g] = group(load_run(pytester.path))
    assert (g.status, g.actuals, g.expected) == ("mismatch", (Int(0),), Int(1))
    assert g.tests == tuple(f"test_param.py::test_n[{n}]" for n in (1, 2, 3, 4))
