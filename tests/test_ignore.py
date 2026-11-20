import dataclasses
import textwrap
from pathlib import Path

import pytest
from test_record import finished, observation
from test_rewrite import rewritten

from xpt import expect
from xpt.group import group
from xpt.options import Options, format_pattern, options, parse_path
from xpt.record import load_run, to_record
from xpt.render import group_diff
from xpt.serialize import serialize
from xpt.value import Dict, Int, Str


@dataclasses.dataclass
class User:
    name: str
    meta: dict[str, object]


def failure(actual: object, expected: object, **how: object) -> str:
    with pytest.raises(AssertionError) as info:
        expect(actual, expected, **how)  # type: ignore
    return str(info.value)


def test_an_ignored_key_passes_whatever_its_value():
    expect({"id": 7, "name": "a"}, {"id": 1, "name": "a"}, ignore=['value["id"]'])


def test_an_ignored_key_may_be_missing_from_the_expected_value():
    expect({"id": 7, "name": "a"}, {"name": "a"}, ignore=['value["id"]'])


def test_a_mismatch_beside_an_ignored_key_still_fails_and_leaves_it_out():
    message = failure({"id": 7, "name": "b"}, {"id": 1, "name": "a"}, ignore=['value["id"]'])
    assert message == textwrap.dedent("""\
        expect() mismatch (ignore=value["id"]):
          value["name"]: "a" -> "b"
          1 changed, 0 unchanged""")


def test_the_wildcard_ignores_a_key_in_every_item():
    rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    expect(rows, [{"v": "a"}, {"v": "b"}], ignore=['value[...]["id"]'])


def test_a_path_through_a_dataclass_field_reaches_a_dict_inside_it():
    expect(User("a", {"at": 1}), User("a", {"at": 2}), ignore=['value.meta["at"]'])


def test_the_generated_value_leaves_ignored_keys_out(recorder):
    with pytest.raises(AssertionError) as info:
        expect({"id": 7, "name": "a"}, ignore=['value["id"]'])
    assert 'Actual: {"name": "a"}' in str(info.value)


@pytest.mark.parametrize(
    "text",
    ["value.id", 'value["a"]', "value[0]", 'value["rows"][...]["id"]', "value[None]"],
)
def test_a_path_reads_back_the_way_it_prints(text: str):
    assert format_pattern(parse_path(text)) == text


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("id", "ignore path 'id' is not a path xpt can read."),
        ("value[(1, 2)]", "ignore path 'value[(1, 2)]' is not a path xpt can read."),
        ("value[", "ignore path 'value[' is not a path xpt can read."),
        ("value", "ignore path 'value' would ignore the whole value. Name a key inside it."),
    ],
)
def test_an_unreadable_path_is_refused_with_how_to_write_one(text: str, message: str):
    with pytest.raises(ValueError) as info:
        parse_path(text)
    assert str(info.value).startswith(message)


def test_ignore_must_be_a_list_of_paths_not_one_string():
    with pytest.raises(TypeError, match=r"ignore must be a list of paths"):
        expect(1, 1, ignore='value["id"]')


def test_ignoring_a_list_item_is_refused_with_the_alternatives():
    message = "ignore path value[1] names an item of a list, and ignore removes only dict entries."
    with pytest.raises(ValueError, match=r"Pass unordered=True") as info:
        expect([1, 2], [1, 3], ignore=["value[1]"])
    assert str(info.value).startswith(message)


def test_ignoring_a_dataclass_field_is_refused_because_xpt_must_write_it():
    with pytest.raises(ValueError, match=r"dataclasses.asdict\(value\)") as info:
        expect(User("a", {}), User("b", {}), ignore=["value.name"])
    assert str(info.value).startswith("ignore path value.name names a field of User")


def test_ignore_is_recorded_so_review_diffs_and_accept_use_it(tmp_path: Path):
    run = "20260101T000000-aaaa"
    how = options(0.0, 0.0, ['value["id"]'], False)
    seen = observation(
        run,
        expected=Dict(((Str("n"), Int(1)),)),
        actual=Dict(((Str("n"), Int(2)),)),
        options=how,
    )
    finished(tmp_path, run, seen)
    [loaded] = load_run(tmp_path)
    assert loaded == seen
    assert to_record(seen)["ignore"] == ['value["id"]']
    assert group([loaded])[0].options == how
    assert group_diff(group([loaded])[0]) is not None


def test_a_plain_observation_records_no_ignore_field():
    assert "ignore" not in to_record(observation("20260101T000000-aaaa", options=Options()))


def test_accept_keeps_the_ignore_keyword_after_the_expected_value():
    src = "def test_it():\n    expect(compute(), ignore=['value[\"id\"]'])\n"
    text = rewritten(src, serialize({"name": "a"}))
    assert (
        text == 'def test_it():\n    expect(compute(), {"name": "a"}, ignore=[\'value["id"]\'])\n'
    )
