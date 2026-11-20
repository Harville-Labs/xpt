import textwrap

import pytest

from xpt.convert import Skip, convert_source


def converted(source: str) -> str:
    plan = convert_source("test_it.py", textwrap.dedent(source).encode("utf-8"))
    assert plan.skipped == (), plan.skipped
    return plan.data.decode("utf-8")


def skipped(source: str) -> list[str]:
    data = textwrap.dedent(source).encode("utf-8")
    plan = convert_source("test_it.py", data)
    assert plan.data == data
    assert plan.converted == ()
    return [f"{s.line}: {s.reason}" for s in plan.skipped]


def test_an_assert_equal_to_a_literal_becomes_an_expect_call_and_the_import_is_added():
    text = converted(
        """\
        import json


        def test_it():
            assert json.loads("[1]") == [1]
        """
    )
    assert text == textwrap.dedent(
        """\
        import json
        from xpt import expect


        def test_it():
            expect(json.loads("[1]"), [1])
        """
    )


def test_a_literal_on_the_left_becomes_the_expected_argument():
    text = converted("from xpt import expect\n\ndef test_it():\n    assert {'a': 1} == f()\n")
    assert text == "from xpt import expect\n\ndef test_it():\n    expect(f(), {'a': 1})\n"


@pytest.mark.parametrize(
    "literal",
    [
        "None",
        "True",
        "-3",
        "'text'",
        '"""a\nb"""',
        "(1, 'a')",
        "{1, 2}",
        "set()",
        "{'k': [None, (1,)]}",
        "[\n        1,\n        2,\n    ]",
    ],
)
def test_every_kind_of_exact_literal_is_kept_byte_for_byte(literal: str):
    text = converted(f"from xpt import expect\n\ndef test_it():\n    assert f() == {literal}\n")
    assert text == f"from xpt import expect\n\ndef test_it():\n    expect(f(), {literal})\n"


def test_other_lines_comments_and_crlf_newlines_are_kept():
    source = (
        "# header\r\n"
        "from xpt import expect  # used\r\n"
        "\r\n"
        "def test_it():  # é\r\n"
        "    x = 1  # keep\r\n"
        "    assert x + 1 == 2  # trailing\r\n"
        "    assert x\r\n"
    )
    plan = convert_source("test_it.py", source.encode("utf-8"))
    assert plan.data.decode("utf-8") == source.replace("assert x + 1 == 2", "expect(x + 1, 2)")
    assert plan.converted == (6,)


def test_a_byte_order_mark_and_non_ascii_text_before_the_assert_are_kept():
    source = "﻿def test_it():\n    assert 'é' + f() == 'éa'\n"
    plan = convert_source("test_it.py", source.encode("utf-8"))
    assert plan.data.decode("utf-8") == (
        "﻿from xpt import expect\ndef test_it():\n    expect('é' + f(), 'éa')\n"
    )


def test_a_multi_line_assert_keeps_the_lines_of_its_expression():
    text = converted(
        """\
        from xpt import expect

        def test_it():
            assert (
                compute(
                    1,
                    2,
                )
                == [3]
            )
        """
    )
    assert "    expect(compute(\n            1,\n            2,\n        ), [3])\n" in text


def test_the_import_goes_after_the_docstring_and_future_imports_when_the_file_has_no_others():
    text = converted('"""Doc."""\n\nfrom __future__ import annotations\n\nassert f() == 1\n')
    assert text == (
        '"""Doc."""\n\nfrom __future__ import annotations\nfrom xpt import expect\n\n'
        "expect(f(), 1)\n"
    )


def test_the_import_goes_above_a_decorated_first_function_and_below_a_shebang():
    text = converted("#!/usr/bin/env python\n@mark\ndef test_it():\n    assert f() == 1\n")
    assert text == (
        "#!/usr/bin/env python\nfrom xpt import expect\n@mark\ndef test_it():\n    expect(f(), 1)\n"
    )


def test_the_import_joins_an_import_line_that_also_holds_the_first_statement():
    text = converted("import os; assert os.sep == '/'")
    assert text == "import os; from xpt import expect; expect(os.sep, '/')"


def test_asserts_that_are_not_an_equality_are_left_alone_without_a_report():
    source = "def test_it():\n    assert f()\n    assert f() != 1\n    assert f() == 1 or g()\n"
    assert skipped(source) == []


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        ("assert f() == 1, 'why'", "the assert has a message, which expect() has no place for."),
        ("assert f() == 1 == g()", "a chained comparison has no single expected value."),
        ("assert f() == g()", "neither side is a literal."),
        ("assert 1 == 1", "both sides are literals, so there is no actual value."),
        (
            "assert f() == [1.5]",
            "the literal holds a float, and == on floats is exact. "
            "Convert it by hand if exact equality is meant, or use expect(..., atol=).",
        ),
        (
            "assert f() == 2j",
            "the literal holds a complex number, and == on floats is exact. "
            "Convert it by hand if exact equality is meant, or use expect(..., atol=).",
        ),
        ("assert f() == b'x'", "the literal holds bytes, which xpt cannot record."),
        ("assert f() == (  # why\n        1)", "a comment inside the assert would be lost."),
    ],
)
def test_an_assert_that_cannot_convert_safely_is_skipped_with_its_reason(
    statement: str, reason: str
):
    source = f"from xpt import expect\n\ndef test_it():\n    {statement}\n"
    assert skipped(source) == [f"4: {reason}"]


def test_a_file_that_binds_expect_to_something_else_is_left_unchanged():
    source = "def test_it(expect):\n    assert f() == 1\n    assert g() == 2\n"
    reason = "line 1 binds the name expect, so xpt's expect cannot be added."
    assert skipped(source) == [f"2: {reason}", f"3: {reason}"]


def test_a_file_that_already_imports_expect_gets_no_second_import():
    text = converted("from xpt import expect\n\ndef test_it():\n    assert f() == 1\n")
    assert text.count("import") == 1


def test_converted_and_skipped_asserts_in_one_file_are_both_reported():
    plan = convert_source(
        "test_it.py", b"def test_it():\n    assert f() == 1\n    assert f() == g()\n"
    )
    assert plan.converted == (2,)
    assert plan.skipped == (Skip("test_it.py", 3, "neither side is a literal."),)


def test_a_file_that_does_not_parse_is_skipped_whole():
    plan = convert_source("test_it.py", b"def test_it(:\n    assert f() == 1\n")
    [skip] = plan.skipped
    assert skip.line is None
    assert skip.reason.startswith("test_it.py does not parse (")


def test_a_file_not_encoded_as_utf_8_is_skipped_whole():
    data = b"# -*- coding: latin-1 -*-\nassert f() == '\xe9'\n"
    plan = convert_source("test_it.py", data)
    assert plan.data == data
    assert plan.skipped == (
        Skip(
            "test_it.py",
            None,
            "test_it.py is encoded as iso-8859-1. xpt convert writes only UTF-8 files.",
        ),
    )
