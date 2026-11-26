import json
import math
from dataclasses import dataclass, field

import pytest
from hypothesis import given
from values import Point, values

from xpt.serialize import UnsupportedValue, serialize
from xpt.value import (
    Bool,
    Dataclass,
    Dict,
    Float,
    Int,
    InvalidValue,
    Null,
    Seq,
    Set,
    Str,
    from_json,
    to_json,
)


def roundtrip(obj: object):
    return from_json(json.loads(json.dumps(to_json(serialize(obj)))))


def test_scalars_keep_their_exact_type():
    assert serialize("a") == Str("a")
    assert serialize(1) == Int(1)
    assert serialize(True) == Bool(True)
    assert serialize(1.5) == Float("1.5")
    assert serialize(None) == Null()


def test_bool_and_int_are_different_values():
    assert serialize(True) != serialize(1)


def test_containers_keep_their_kind_and_order():
    assert serialize([1, (2,)]) == Seq("list", (Int(1), Seq("tuple", (Int(2),))))
    assert serialize({"b": 1, "a": 2}) == Dict(((Str("b"), Int(1)), (Str("a"), Int(2))))


def test_set_members_are_sorted_whatever_the_insertion_order():
    assert serialize({"b", "a", "c"}) == serialize({"c", "a", "b"})
    assert serialize(frozenset({3, 1, 2})) == Set("frozenset", (Int(1), Int(2), Int(3)))


def test_set_order_does_not_follow_iteration_order():
    assert list({9, 1}) == [9, 1]
    assert serialize({9, 1}) == Set("set", (Int(1), Int(9)))


def test_dataclass_records_module_qualname_and_fields():
    assert serialize(Point(1, "a")) == Dataclass("values:Point", (("x", Int(1)), ("y", Str("a"))))


@pytest.mark.parametrize("v", [math.nan, math.inf, -math.inf, -0.0, 0.1, 1e300])
def test_special_floats_roundtrip_through_json(v: float):
    back = roundtrip(v)
    assert isinstance(back, Float)
    assert repr(back.v) == repr(v)


@given(values)
def test_every_supported_value_roundtrips_through_json(obj: object):
    assert roundtrip(obj) == serialize(obj)


def test_a_shared_reference_is_not_a_cycle():
    shared = [1]
    assert serialize([shared, shared]) == serialize([[1], [1]])


def test_a_cycle_is_unsupported_and_names_the_path():
    loop: list[object] = [1]
    loop.append({"next": loop})
    with pytest.raises(UnsupportedValue, match=r'value\[1\]\["next"\] contains itself'):
        serialize(loop)


def test_an_unsupported_type_names_the_path_and_the_type():
    with pytest.raises(UnsupportedValue) as e:
        serialize({"handler": print})
    assert str(e.value) == (
        'value["handler"] is a builtins:builtin_function_or_method; xpt cannot record it. '
        "Register its class with xpt.register or expect a field of it instead."
    )


def test_builtin_subclasses_are_unsupported():
    class Name(str):
        pass

    with pytest.raises(UnsupportedValue, match="Name"):
        serialize([Name("a")])


def test_dataclass_that_compares_by_identity_is_unsupported():
    @dataclass(eq=False)
    class Handle:
        n: int

    with pytest.raises(UnsupportedValue, match="eq=False"):
        serialize(Handle(1))


def test_dataclass_with_a_non_init_field_is_unsupported():
    @dataclass
    class Cached:
        n: int
        total: int = field(init=False, default=0)

    with pytest.raises(UnsupportedValue, match="'total'"):
        serialize(Cached(1))


def test_json_is_plain_except_where_json_cannot_say_the_type():
    obj = {"a": [1, 2.5, True, None]}
    assert to_json(serialize(obj)) == {"dict": {"a": [1, 2.5, True, None]}}
    assert to_json(serialize((1,))) == {"tuple": [1]}
    assert to_json(serialize({1: "x"})) == {"dict": [[1, "x"]]}
    assert to_json(serialize(math.nan)) == {"float": "nan"}
    assert to_json(serialize(Point(1, "a"))) == {"values:Point": {"x": 1, "y": "a"}}


def test_an_integral_float_stays_a_float_through_json_text():
    assert from_json(json.loads(json.dumps(to_json(serialize(1.0))))) == Float("1.0")


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"date": "2026-01-01"},
        {"tuple": [], "set": []},
        {"tuple": {}},
        {"float": "1.5"},
        {"float": "eval()"},
        {"dict": [[1]]},
        {"dict": "a"},
        {"m:C": [1]},
        math.inf,
        object(),
    ],
)
def test_malformed_json_is_rejected(data: object):
    with pytest.raises(InvalidValue):
        from_json(data)


def test_a_complex_number_is_a_complex_call_by_keyword():
    assert serialize(1 - 2j) == Dataclass(
        "builtins:complex", (("real", Float("1.0")), ("imag", Float("-2.0")))
    )
