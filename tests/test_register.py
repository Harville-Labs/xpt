from dataclasses import dataclass

import pytest

import xpt
from xpt import expect
from xpt.serialize import UnsupportedValue, serialize
from xpt.source import to_source


class Money:
    def __init__(self, cents: int, currency: str = "USD") -> None:
        self.cents = cents
        self.currency = currency

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and (self.cents, self.currency) == (
            other.cents,
            other.currency,
        )


xpt.register(Money, lambda m: {"cents": m.cents, "currency": m.currency})

NAMES = {f"{__name__}:Money": "Money"}


def test_a_registered_class_is_written_as_a_keyword_call_that_rebuilds_it():
    source = to_source(serialize({"price": Money(250)}), names=NAMES)
    assert source == '{"price": Money(cents=250, currency="USD")}'
    assert eval(source, {"Money": Money}) == {"price": Money(250)}


def test_a_registered_class_is_compared_with_its_own_eq_and_diffed_by_field():
    expect(Money(250), Money(250))
    with pytest.raises(AssertionError) as info:
        expect(Money(250, "EUR"), Money(250))
    assert '  value.currency: "USD" -> "EUR"' in str(info.value).splitlines()


@dataclass
class Span:
    start: int
    end: int
    cache: object = None


xpt.register(Span, lambda s: {"start": s.start, "end": s.end})


def test_a_registration_wins_over_the_dataclass_fields():
    names = {f"{__name__}:Span": "Span"}
    assert to_source(serialize(Span(1, 3, object())), names=names) == "Span(start=1, end=3)"


class Opaque:
    pass


def test_a_class_without_eq_is_refused_when_registered():
    with pytest.raises(TypeError) as info:
        xpt.register(Opaque, lambda o: {})
    assert str(info.value) == (
        f"{__name__}.Opaque has no __eq__, so a rebuilt one never equals the original. "
        "Define __eq__ before registering it."
    )


class Bad(Money):
    pass


xpt.register(Bad, lambda b: {"class": b.cents})


class NotADict(Money):
    pass


xpt.register(NotADict, lambda n: [n.cents])  # type: ignore


def test_fields_that_are_not_keyword_arguments_are_unsupported():
    with pytest.raises(UnsupportedValue, match="field 'class' is not a keyword argument"):
        serialize(Bad(1))
    with pytest.raises(UnsupportedValue, match="registered fields returned a list, not a dict"):
        serialize(NotADict(1))
