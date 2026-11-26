import re
import textwrap

import pytest

from xpt import expect
from xpt.expect import ExpectationFailed, Raises
from xpt.record import Recorder, load_run
from xpt.site import CallSite, Unlocatable
from xpt.value import Dataclass, Seq, Str


def throw(error: BaseException) -> None:
    raise error


def recorded(rec: Recorder):
    rec.finish()
    return load_run(rec.root)


def test_no_function_or_an_exception_first_makes_a_context_manager():
    assert isinstance(expect.raises(), Raises)
    assert isinstance(expect.raises(ValueError("a"), subclass=True), Raises)
    assert expect.raises(lambda: throw(ValueError("a")), ValueError("a")) is None


def test_an_equal_exception_in_the_block_is_suppressed():
    with expect.raises(ValueError("invalid literal for int() with base 10: 'x'")):
        int("1")
        int("x")
    with expect.raises(LookupError("k"), subclass=True):
        {}["k"]


def test_a_missing_expectation_fails_and_records_the_exception(recorder: Recorder):
    with pytest.raises(ExpectationFailed) as info, expect.raises():
        throw(ValueError("bad"))
    assert str(info.value) == 'expect.raises() has no expected value. Raised: ValueError("bad")'
    assert info.value.__suppress_context__
    [o] = recorded(recorder)
    assert o.status == "missing"
    assert o.actual == Dataclass("builtins:ValueError", (("*", Seq("tuple", (Str("bad"),))),))
    assert isinstance(o.site, CallSite)
    assert (o.site.callee, o.site.arg_sha) == ("expect.raises", "")


def test_a_different_exception_is_a_mismatch():
    with pytest.raises(ExpectationFailed) as info, expect.raises(ValueError("old")):
        throw(ValueError("new"))
    assert str(info.value) == textwrap.dedent("""\
        expect.raises() mismatch:
          value.args[0]: "old" -> "new"
          1 changed, 0 unchanged""")


def test_a_block_that_finishes_fails_with_status_returned_and_no_value(recorder: Recorder):
    with pytest.raises(ExpectationFailed) as info, expect.raises(ValueError("bad")):
        pass
    message = (
        'expect.raises(): the with block finished without raising, expected ValueError("bad").'
    )
    assert str(info.value) == message
    [o] = recorded(recorder)
    assert (o.status, o.actual, o.detail) == ("returned", None, message)
    assert o.expected is not None


def test_interrupts_and_failed_expectations_pass_through():
    with pytest.raises(KeyboardInterrupt), expect.raises():
        throw(KeyboardInterrupt())
    failed = pytest.raises(ExpectationFailed, match=r"expect\(\) mismatch")
    with failed, expect.raises(AssertionError("x")):
        expect(1, 2)
    with expect.raises(KeyboardInterrupt()):
        throw(KeyboardInterrupt())
    with expect.raises(SystemExit(2)):
        throw(SystemExit(2))


def test_the_expected_exception_is_given_once_and_as_an_instance():
    with pytest.raises(TypeError, match="takes the expected exception once"):
        expect.raises(ValueError("a"), ValueError("a"))  # type: ignore
    with pytest.raises(TypeError, match=re.escape("such as ValueError('message')")):
        expect.raises(ValueError)
    with pytest.raises(TypeError, match="takes a function to call first"):
        expect.raises(3)  # type: ignore


def test_a_context_manager_made_outside_a_with_statement_is_unlocatable(recorder: Recorder):
    made = expect.raises()
    with pytest.raises(ExpectationFailed), made:
        throw(ValueError("a"))
    [o] = recorded(recorder)
    assert isinstance(o.site, Unlocatable)
    assert "outside a with statement" in o.site.reason


PROJECT = """\
from contextlib import nullcontext

from xpt import expect


def test_plain():
    with expect.raises():
        int("1")
        int("x")


def test_keyword():
    with expect.raises(subclass=True):
        {}["k"]


def test_items():
    with (
        nullcontext(),
        expect.raises(),
        expect.raises(),
    ):
        raise KeyError("inner")


def test_shared_line():
    with expect.raises(), nullcontext():
        raise TypeError("t")


def test_mismatch():
    with expect.raises(ValueError("old")):
        raise ValueError("new")


def test_both_forms():
    expect.raises(lambda: int("y"))
    with expect.raises():
        raise ValueError("z")


def test_finishes():
    with expect.raises():
        pass


def test_comment():
    with expect.raises(  # a comment keeps the call as written
        subclass=True,
    ):
        {}["j"]"""

ACCEPTED = """\
def test_plain():
    with expect.raises(ValueError("invalid literal for int() with base 10: 'x'")):
        int("1")
        int("x")


def test_keyword():
    with expect.raises(KeyError("k"), subclass=True):
        {}["k"]


def test_items():
    with (
        nullcontext(),
        expect.raises(),
        expect.raises(KeyError("inner")),
    ):
        raise KeyError("inner")


def test_shared_line():
    with expect.raises(TypeError("t")), nullcontext():
        raise TypeError("t")


def test_mismatch():
    with expect.raises(ValueError("new")):
        raise ValueError("new")


def test_both_forms():
    expect.raises(lambda: int("y"), ValueError("invalid literal for int() with base 10: 'y'"))
    with expect.raises(ValueError("z")):
        raise ValueError("z")


def test_finishes():
    with expect.raises():
        pass


def test_comment():
    with expect.raises(KeyError("j"),   # a comment keeps the call as written
        subclass=True,
    ):
        {}["j"]"""


def _project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)
    monkeypatch.delenv("XPT_REPORT", raising=False)
    test = pytester.path / "test_sample.py"
    test.write_text(PROJECT, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    return test


def test_accept_writes_the_exception_as_the_with_items_first_argument(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    test = _project(pytester, monkeypatch)
    first = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    first.assert_outcomes(failed=8)
    assert "  expect.raises(): the with block finished without raising." in first.outlines
    accepted = pytester.run("xpt", "accept", "--all")
    assert any("finished without raising" in line for line in accepted.outlines)
    # The block in test_both_forms runs once the call form before it passes.
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=3, passed=5)
    pytester.run("xpt", "accept", "--all")
    assert test.read_text(encoding="utf-8").endswith(ACCEPTED)
    again = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    # The innermost item caught the KeyError, so the outer one in test_items now sees none.
    again.assert_outcomes(failed=2, passed=6)
    assert any("test_items" in line for line in again.outlines if "FAILED" in line)


def test_expect_create_writes_the_block_form(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    test = _project(pytester, monkeypatch)
    pytester.runpytest_subprocess("-p", "no:cacheprovider", "--expect-create")
    source = test.read_text(encoding="utf-8")
    assert '    with expect.raises(TypeError("t")), nullcontext():\n' in source
    assert '    with expect.raises(ValueError("old")):\n' in source
    assert "    with expect.raises():\n        pass\n" in source


def test_accept_refuses_a_with_item_whose_source_changed(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    test = _project(pytester, monkeypatch)
    pytester.runpytest_subprocess("-p", "no:cacheprovider")
    test.write_text(
        PROJECT.replace('expect.raises(ValueError("old"))', 'expect.raises(ValueError("edited"))'),
        encoding="utf-8",
    )
    accepted = pytester.run("xpt", "accept", "--all")
    assert any("changed since the run" in line for line in accepted.outlines)
    assert 'expect.raises(ValueError("edited"))' in test.read_text(encoding="utf-8")
