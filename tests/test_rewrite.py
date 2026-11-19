import shutil
import subprocess

from hypothesis import given, settings
from hypothesis import strategies as st
from values import Point, values

from xpt.group import Group
from xpt.rewrite import rewrite
from xpt.serialize import serialize
from xpt.site import CallSite, arg_sha, find_call, scopes
from xpt.value import Dataclass, Int, Seq, Str, Value


def target(source: str, actual: Value) -> Group:
    text = source.removeprefix("\ufeff")
    found = scopes("test_it.py", text)
    assert not isinstance(found, SyntaxError)
    call = find_call(found["test_it"], "expect", 0)
    assert call is not None
    sha = arg_sha(text, call)
    site = CallSite("test_it.py", "test_it", "expect", 0, sha, call.lineno)
    status = "mismatch" if sha else "missing"
    return Group("abcd", site, status, (actual,), None, ("test_it.py::test_it",), "")


def rewritten(source: str, actual: Value) -> str:
    data = source.encode("utf-8")
    plan = rewrite("test_it.py", data, [target(source, actual)])
    [outcome] = plan.outcomes
    assert (outcome.status, outcome.reason) == ("accepted", "")
    return plan.data.decode("utf-8")


def ruff_changes(src: str) -> str:
    ruff = shutil.which("ruff")
    assert ruff, "ruff must be installed in the dev environment"
    done = subprocess.run(
        [ruff, "format", "--isolated", "--line-length", "100", "--diff", "-"],
        input=src,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    return done.stdout


LONG = Seq("list", tuple(Int(10**8 + i) for i in range(12)))


def test_a_literal_too_long_for_the_line_splits_the_call_the_way_ruff_does():
    source = "def test_it():\n    expect(compute(), [], _depth=0)\n"
    text = rewritten(source, LONG)
    assert text == (
        "def test_it():\n"
        "    expect(\n"
        "        compute(),\n"
        "        [\n" + "".join(f"            {10**8 + i},\n" for i in range(12)) + "        ],\n"
        "        _depth=0,\n"
        "    )\n"
    )
    assert ruff_changes(text) == ""


def test_a_literal_that_fits_inside_the_split_call_stays_on_one_line():
    short = Seq("list", tuple(Int(10**8 + i) for i in range(6)))
    text = rewritten("def test_it():\n    expect(compute_the_value_now())\n", short)
    flat = ", ".join(str(10**8 + i) for i in range(6))
    assert (
        text == f"def test_it():\n    expect(\n        compute_the_value_now(), [{flat}]\n    )\n"
    )
    assert ruff_changes(text) == ""


def test_a_multiline_string_is_written_triple_quoted_in_an_exploded_call():
    text = rewritten("def test_it():\n    expect(render())\n", Str("line one\nline two\n"))
    assert text == (
        "def test_it():\n    expect(\n        render(),\n"
        '        """line one\nline two\n""",\n    )\n'
    )
    assert ruff_changes(text) == ""


def test_an_exploded_call_keeps_its_magic_trailing_comma():
    source = "def test_it():\n    expect(\n        1,\n        2,\n    )\n"
    assert rewritten(source, Int(1)) == source.replace("2,", "1,")


def test_a_keyword_expected_argument_stays_a_keyword():
    source = "def test_it():\n    expect(1, expected=2)\n"
    assert rewritten(source, Int(1)) == "def test_it():\n    expect(1, expected=1)\n"


def test_non_ascii_text_before_the_call_on_its_line_does_not_shift_the_edit():
    source = 'def test_it():\n    s = "héllo ✓ 日本"; expect(len(s), 0)\n'
    assert rewritten(source, Int(10)) == source.replace("expect(len(s), 0)", "expect(len(s), 10)")


def test_a_crlf_file_stays_crlf_including_new_lines():
    source = "def test_it():\r\n    expect(compute(), 0)\r\n    pass\r\n"
    text = rewritten(source, LONG)
    assert text.count("\r\n") == text.count("\n") > 3
    assert ruff_changes(text.replace("\r\n", "\n")) == ""


def test_a_byte_order_mark_is_kept():
    source = "﻿def test_it(): expect(2, 1)\n"
    assert rewritten(source, Int(2)) == "﻿def test_it(): expect(2, 2)\n"


def test_a_comment_inside_the_call_is_kept_by_writing_only_the_argument():
    source = "def test_it():\n    expect(\n        1,  # actual\n        0,  # expected\n    )\n"
    assert rewritten(source, Int(1)) == source.replace("0,  #", "1,  #")


def test_a_missing_argument_in_a_call_with_a_comment_is_inserted_after_the_actual():
    source = "def test_it():\n    expect(\n        1,  # actual\n    )\n"
    assert rewritten(source, Int(1)) == source.replace("1,  #", "1, 1,  #")


def test_a_dataclass_is_written_by_the_name_the_module_imports():
    source = "from values import Point\n\ndef test_it():\n    expect(Point(1, 2))\n"
    point = serialize(Point(1, 2))
    assert rewritten(source, point).endswith("    expect(Point(1, 2), Point(x=1, y=2))\n")


def test_a_dataclass_the_module_does_not_import_is_skipped():
    source = "def test_it():\n    expect(make())\n"
    point = Dataclass("values:Point", (("x", Int(1)),))
    plan = rewrite("test_it.py", source.encode("utf-8"), [target(source, point)])
    [outcome] = plan.outcomes
    assert outcome.status == "skipped"
    assert outcome.reason == (
        "test_it.py:2 needs the name Point, which test_it.py does not import at module level. "
        "Import it and accept again."
    )
    assert plan.data == source.encode("utf-8")


def test_a_file_declaring_another_encoding_is_refused():
    source = "# -*- coding: latin-1 -*-\ndef test_it():\n    expect(1, 2)\n"
    group = target(source, Int(1))
    plan = rewrite("test_it.py", source.encode("latin-1"), [group])
    [outcome] = plan.outcomes
    assert (outcome.status, outcome.reason) == (
        "skipped",
        "test_it.py is encoded as iso-8859-1. xpt accept writes only UTF-8 files.",
    )


def test_a_file_that_is_not_utf_8_is_refused():
    source = "def test_it():\n    expect(1, 2)\n"
    data = source.encode("utf-8") + b"# \xff\n"
    [outcome] = rewrite("test_it.py", data, [target(source, Int(1))]).outcomes
    assert outcome.status == "skipped"
    assert outcome.reason.startswith("test_it.py is not valid UTF-8 (")


def test_a_call_that_no_longer_exists_is_stale():
    group = target("def test_it():\n    expect(1, 2)\n", Int(1))
    [outcome] = rewrite("test_it.py", b"def test_it():\n    pass\n", [group]).outcomes
    assert (outcome.status, outcome.reason) == (
        "stale",
        "test_it.py no longer has expect() call 1 in test_it. Run the tests again.",
    )


@settings(max_examples=60, deadline=None)
@given(values, st.sampled_from([4, 8]), st.integers(1, 60))
def test_every_accepted_value_leaves_the_call_ruff_formatted(obj: object, indent: int, size: int):
    blocks = ["from values import Point", "", "", "def test_it():", "    if x:"][: 3 + indent // 4]
    source = "\n".join([*blocks, f"{' ' * indent}expect({'a' * size})"]) + "\n"
    text = rewritten(source, serialize(obj))
    assert ruff_changes(text) == ""
