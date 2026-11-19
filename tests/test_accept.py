import os
import textwrap
from pathlib import Path

import pytest

from xpt.accept import AcceptError, AcceptResult, accept
from xpt.group import Group, group
from xpt.record import load_run


def failing_run(pytester: pytest.Pytester, source: str, failed: int) -> tuple[Group, ...]:
    source_file(pytester).write_text(textwrap.dedent(source), encoding="utf-8")
    pytester.makeini("[pytest]\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=failed)
    return group(load_run(pytester.path))


def accepted(pytester: pytest.Pytester, source: str, failed: int) -> str:
    result = accept(pytester.path, failing_run(pytester, source, failed))
    assert {o.status for o in result.outcomes} == {"accepted"}, result
    assert result.changed == ("test_it.py",)
    text = source_file(pytester).read_text(encoding="utf-8")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)
    return text


def source_file(pytester: pytest.Pytester) -> Path:
    return pytester.path / "test_it.py"


def test_a_missing_expectation_is_inserted_as_the_second_argument(pytester: pytest.Pytester):
    text = accepted(
        pytester,
        """\
        from xpt import expect

        def test_it():
            expect({"a": [1, 2]})  # keep me
        """,
        failed=1,
    )
    assert text == textwrap.dedent(
        """\
        from xpt import expect

        def test_it():
            expect({"a": [1, 2]}, {"a": [1, 2]})  # keep me
        """
    )


def test_a_mismatched_expectation_is_replaced_in_place(pytester: pytest.Pytester):
    text = accepted(
        pytester,
        """\
        from xpt import expect

        def test_it():
            expect(sorted("cab"), ["x"])
        """,
        failed=1,
    )
    assert '    expect(sorted("cab"), ["a", "b", "c"])\n' in text


def test_two_expectations_in_one_test_are_both_written_in_one_pass(pytester: pytest.Pytester):
    groups = failing_run(
        pytester,
        """\
        from xpt import expect

        def test_it():
            try:
                expect(1 + 1)
            finally:
                expect("é" * 3, "wrong")
        """,
        failed=1,
    )
    result = accept(pytester.path, groups)
    assert [o.status for o in result.outcomes] == ["accepted", "accepted"]
    text = source_file(pytester).read_text(encoding="utf-8")
    assert "        expect(1 + 1, 2)\n" in text
    assert '        expect("é" * 3, "ééé")\n' in text


def test_two_calls_on_one_line_are_told_apart_by_ordinal(pytester: pytest.Pytester):
    text = accepted(
        pytester,
        """\
        from xpt import expect

        def test_it():
            try:
                expect(1, 0)
            finally:
                expect(2, 0); expect(3, 3)
        """,
        failed=1,
    )
    assert "        expect(1, 1)\n" in text
    assert "        expect(2, 2); expect(3, 3)\n" in text


def test_a_helper_with_depth_one_is_rewritten_at_its_caller(pytester: pytest.Pytester):
    text = accepted(
        pytester,
        """\
        from xpt import expect

        def check(actual, expected=None, *, label=""):
            expect(actual * 2, expected, _depth=1)

        def test_it():
            check(21, label="half")
        """,
        failed=1,
    )
    assert '    check(21, 42, label="half")\n' in text
    assert "    expect(actual * 2, expected, _depth=1)\n" in text


def test_a_parametrized_test_that_agrees_is_accepted_once(pytester: pytest.Pytester):
    groups = failing_run(
        pytester,
        """\
        import pytest
        from xpt import expect

        @pytest.mark.parametrize("n", [1, 2, 3])
        def test_it(n):
            expect(n > 0)
        """,
        failed=3,
    )
    result = accept(pytester.path, groups)
    assert [o.status for o in result.outcomes] == ["accepted"]
    assert "    expect(n > 0, True)\n" in source_file(pytester).read_text(encoding="utf-8")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=3)


def test_a_conflicting_group_is_skipped_with_its_detail(pytester: pytest.Pytester):
    source = """\
        import pytest
        from xpt import expect

        @pytest.mark.parametrize("n", [1, 2])
        def test_it(n):
            expect(n)
        """
    [g] = failing_run(pytester, source, failed=2)
    before = source_file(pytester).read_bytes()
    result = accept(pytester.path, [g])
    [outcome] = result.outcomes
    assert (outcome.status, outcome.reason) == ("skipped", g.detail)
    assert g.detail.startswith("calls disagree on the actual value.")
    assert (result.changed, source_file(pytester).read_bytes()) == ((), before)


def test_an_expectation_edited_since_the_run_is_stale_and_left_alone(pytester: pytest.Pytester):
    groups = failing_run(
        pytester,
        """\
        from xpt import expect

        def test_it():
            expect(5, 4)
        """,
        failed=1,
    )
    path = source_file(pytester)
    edited = path.read_text(encoding="utf-8").replace("expect(5, 4)", "expect(5, 3)")
    path.write_text(edited, encoding="utf-8")
    result = accept(pytester.path, groups)
    [outcome] = result.outcomes
    assert outcome.status == "stale"
    assert outcome.reason == "test_it.py:4 changed since the run. Run the tests again."
    assert (result.changed, path.read_text(encoding="utf-8")) == ((), edited)


def test_edits_elsewhere_in_the_file_do_not_make_an_expectation_stale(pytester: pytest.Pytester):
    groups = failing_run(
        pytester,
        """\
        from xpt import expect

        def test_it():
            expect(5, 4)
        """,
        failed=1,
    )
    path = source_file(pytester)
    path.write_text("# moved down\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert [o.status for o in accept(pytester.path, groups).outcomes] == ["accepted"]
    assert "    expect(5, 5)\n" in path.read_text(encoding="utf-8")


def test_accepting_the_same_groups_twice_changes_nothing_the_second_time(
    pytester: pytest.Pytester,
):
    groups = failing_run(
        pytester,
        """\
        from xpt import expect

        def test_it():
            try:
                expect([1] * 30)
            finally:
                expect(7, 6)
        """,
        failed=1,
    )
    accept(pytester.path, groups)
    after_first = source_file(pytester).read_bytes()
    again = accept(pytester.path, groups)
    assert [o.status for o in again.outcomes] == ["already_accepted", "already_accepted"]
    assert [o.reason for o in again.outcomes] == ["", ""]
    assert (again.changed, source_file(pytester).read_bytes()) == ((), after_first)


def test_a_held_lock_refuses_and_names_the_lock_file(tmp_path: Path):
    lock = tmp_path / ".xpt" / "accept.lock"
    lock.parent.mkdir()
    lock.write_text("123", encoding="utf-8")
    with pytest.raises(AcceptError) as info:
        accept(tmp_path, [])
    assert str(info.value) == (
        f"{lock.as_posix()} exists, so another xpt accept is running. "
        f"If none is, delete {lock.as_posix()} and run accept again."
    )
    assert lock.read_text(encoding="utf-8") == "123"


def test_accept_releases_its_lock_and_reports_nothing_for_no_groups(tmp_path: Path):
    assert accept(tmp_path, []) == AcceptResult(outcomes=(), changed=())
    assert not (tmp_path / ".xpt" / "accept.lock").exists()
    assert (tmp_path / ".xpt" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_a_site_outside_the_project_is_never_written(pytester: pytest.Pytester, tmp_path: Path):
    [g] = failing_run(
        pytester,
        """\
        from xpt import expect

        def test_it():
            expect(1, 2)
        """,
        failed=1,
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    [outcome] = accept(elsewhere, [g]).outcomes
    assert outcome.status == "skipped"
    assert outcome.reason == (
        f"test_it.py is not a file under {elsewhere.as_posix()}. Run accept from the project root."
    )


def test_a_rewritten_file_keeps_its_permissions(pytester: pytest.Pytester):
    groups = failing_run(
        pytester,
        """\
        from xpt import expect

        def test_it():
            expect(1, 2)
        """,
        failed=1,
    )
    path = source_file(pytester)
    path.chmod(0o640)
    accept(pytester.path, groups)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in pytester.path.iterdir() if p.name.endswith(".tmp")] == []
