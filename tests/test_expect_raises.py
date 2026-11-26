import json
import re
import textwrap

import pytest

from xpt import expect
from xpt.expect import ExpectationFailed
from xpt.record import Recorder, load_run
from xpt.serialize import UnsupportedValue, serialize
from xpt.source import one_line, to_source
from xpt.value import Dataclass, InvalidValue, Ref, Seq, Str, from_json, to_json


class ParseError(Exception):
    pass


class Located(Exception):
    def __init__(self, line: int, col: int) -> None:
        super().__init__(f"{line}:{col}")
        self.line = line


def make() -> object:
    return object()


def throw(error: BaseException) -> None:
    raise error


def fails(*args: object, **kwargs: object) -> ExpectationFailed:
    with pytest.raises(ExpectationFailed) as info:
        expect.raises(*args, **kwargs)  # type: ignore
    return info.value


def recorded(rec: Recorder):
    rec.finish()
    return load_run(rec.root)


def test_an_equal_exception_passes():
    expect.raises(lambda: int("x"), ValueError("invalid literal for int() with base 10: 'x'"))
    expect.raises(lambda: throw(ParseError("bad", 3)), ParseError("bad", 3))
    expect.raises(lambda: throw(KeyError()), KeyError())


def test_a_missing_expectation_fails_and_records_the_exception(recorder: Recorder):
    error = fails(lambda: int("x"))
    assert str(error) == (
        "expect.raises() has no expected value. "
        "Raised: ValueError(\"invalid literal for int() with base 10: 'x'\")"
    )
    [o] = recorded(recorder)
    assert o.status == "missing"
    assert o.actual == Dataclass(
        "builtins:ValueError",
        (("*", Seq("tuple", (Str("invalid literal for int() with base 10: 'x'"),))),),
    )


def test_a_different_message_is_a_mismatch_at_the_args_path():
    error = fails(lambda: throw(ValueError("new")), ValueError("old"))
    assert str(error) == textwrap.dedent("""\
        expect.raises() mismatch:
          value.args[0]: "old" -> "new"
          1 changed, 0 unchanged""")


def test_the_type_must_match_exactly_unless_subclass_is_true():
    error = fails(lambda: {}["k"], LookupError("k"))
    assert str(error).splitlines()[1] == '  value: LookupError("k") -> KeyError("k")'
    expect.raises(lambda: {}["k"], LookupError("k"), subclass=True)


def test_a_subclass_mismatch_records_the_expected_type_so_accept_keeps_it(recorder: Recorder):
    fails(lambda: {}["k"], LookupError("j"), subclass=True)
    [o] = recorded(recorder)
    assert o.actual == Dataclass("builtins:LookupError", (("*", Seq("tuple", (Str("k"),))),))


def test_a_call_that_returns_fails_and_records_what_it_returned(recorder: Recorder):
    with pytest.raises(ExpectationFailed) as info:
        expect.raises(lambda: int("3"), ValueError("bad"))
    error = info.value
    assert str(error) == (
        'expect.raises(): int("3") returned 3 instead of raising, expected ValueError("bad").'
    )
    [o] = recorded(recorder)
    assert (o.status, to_json(o.actual), o.detail) == ("returned", 3, str(error))
    assert o.expected is not None


def test_a_returned_value_xpt_cannot_record_is_named_by_its_type():
    with pytest.raises(ExpectationFailed) as info:
        expect.raises(make)
    assert str(info.value) == (
        "expect.raises(): make() returned a value xpt cannot record (is a builtins:object) "
        "instead of raising."
    )


def test_a_function_xpt_cannot_find_in_source_is_named_plainly():
    assert str(fails(make)).startswith("expect.raises(): the function returned")


def test_notes_are_compared_and_written_through_expect_exception():
    def noted() -> None:
        error = ValueError("v")
        error.add_note("while parsing")
        raise error

    expect.raises(noted, expect.exception(ValueError, "v", notes=["while parsing"]))
    error = fails(noted, ValueError("v"))
    assert str(error).splitlines()[1] == (
        '  value: ValueError("v") -> expect.exception(ValueError, "v", notes=["while parsing"])'
    )


def test_a_class_that_cannot_be_rebuilt_from_its_args_is_written_with_expect_exception():
    value = serialize(Located(1, 2))
    names = {"test_expect_raises:Located": "Located", "xpt:expect.exception": "expect.exception"}
    assert to_source(value, names=names) == 'expect.exception(Located, "1:2")'
    expect.raises(lambda: throw(Located(1, 2)), expect.exception(Located, "1:2"))


def test_an_exception_group_holds_its_children():
    group = ExceptionGroup("many", [ValueError("a"), TypeError("b")])
    assert one_line(serialize(group)) == 'ExceptionGroup("many", [ValueError("a"), TypeError("b")])'
    expect.raises(
        lambda: throw(ExceptionGroup("many", [ValueError("a"), TypeError("b")])),
        ExceptionGroup("many", [ValueError("a"), TypeError("b")]),
    )


def test_ignore_removes_dict_entries_inside_the_args():
    expect.raises(
        lambda: throw(ValueError({"id": 7, "why": "bad"})),
        ValueError({"why": "bad"}),
        ignore=['value.args[0]["id"]'],
    )


def test_ignore_cannot_remove_an_argument():
    with pytest.raises(ValueError, match=r"names an item of a tuple"):
        expect.raises(lambda: throw(ValueError("a")), ValueError("b"), ignore=["value.args[0]"])


def test_interrupts_and_failed_expectations_pass_through():
    with pytest.raises(KeyboardInterrupt):
        expect.raises(lambda: throw(KeyboardInterrupt()))
    with pytest.raises(ExpectationFailed, match="expect\\(\\) mismatch"):
        expect.raises(lambda: expect(1, 2), AssertionError("x"))
    expect.raises(lambda: throw(KeyboardInterrupt()), KeyboardInterrupt())
    expect.raises(lambda: throw(SystemExit(2)), SystemExit(2))


def test_a_class_instead_of_an_instance_is_refused_with_how_to_write_one():
    with pytest.raises(TypeError, match=re.escape("such as ValueError('message')")):
        expect.raises(lambda: int("x"), ValueError)
    with pytest.raises(TypeError, match="takes a function to call first"):
        expect.raises(int("3"))  # type: ignore


def test_an_argument_xpt_cannot_record_is_unsupported():
    with pytest.raises(UnsupportedValue, match=r"value\.args\[0\] is a builtins:object"):
        expect.raises(lambda: throw(ValueError(object())))


def test_plain_expect_compares_exceptions_by_type_args_and_notes():
    expect({"error": ValueError("a")}, {"error": ValueError("a")})
    with pytest.raises(ExpectationFailed):
        expect(ValueError("a"), TypeError("a"))


def test_recorded_exceptions_round_trip_through_json():
    value = serialize(expect.exception(Located, "1:2", notes=["n"]))
    assert value == Dataclass(
        "xpt:expect.exception",
        (
            ("", Ref("test_expect_raises:Located")),
            ("*", Seq("tuple", (Str("1:2"),))),
            ("notes", Seq("list", (Str("n"),))),
        ),
    )
    assert from_json(json.loads(json.dumps(to_json(value)))) == value


def test_positional_arguments_must_be_a_tuple_in_a_record():
    with pytest.raises(InvalidValue, match="positional arguments as a tuple"):
        from_json({"builtins:ValueError": {"*": ["a"]}})


def test_expect_exception_needs_an_exception_class():
    with pytest.raises(TypeError, match="takes an exception class first"):
        expect.exception(int, 1)  # type: ignore


PROJECT = """\
import json

from xpt import expect


class ParseError(Exception):
    pass


def parse(text):
    if text == "3":
        return 3
    raise ParseError("bad input", text)


def test_builtin():
    expect.raises(lambda: int("x"))


def test_project():
    expect.raises(lambda: parse("x"))


def test_unimportable():
    expect.raises(lambda: json.loads(""))


def test_returned():
    expect.raises(lambda: parse("3"))
"""


def test_accept_writes_each_exception_and_skips_the_ones_it_cannot(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)
    monkeypatch.delenv("XPT_REPORT", raising=False)
    test = pytester.path / "test_sample.py"
    test.write_text(PROJECT, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    first = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    first.assert_outcomes(failed=4)
    assert '  expect.raises(): parse("3") returned 3 instead of raising.' in first.outlines
    accepted = pytester.run("xpt", "accept", "--all")
    assert any(
        "needs the name JSONDecodeError from json.decoder" in line for line in accepted.outlines
    )
    assert any(
        "returned 3 instead of raising. Fix the code under test" in line
        for line in accepted.outlines
    )
    source = test.read_text(encoding="utf-8")
    assert (
        '    expect.raises(lambda: int("x"), '
        "ValueError(\"invalid literal for int() with base 10: 'x'\"))\n"
    ) in source
    assert '    expect.raises(lambda: parse("x"), ParseError("bad input", "x"))\n' in source
    assert '    expect.raises(lambda: json.loads(""))\n' in source
    assert '    expect.raises(lambda: parse("3"))\n' in source
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=2, passed=2)
