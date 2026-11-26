"""Standard-library values that xpt records natively: dates and times, UUIDs, decimals, paths,
and enum members.

Each becomes the same constructor-call node a dataclass uses, so the diff, grouping, and accept
need no code of their own for it. This module holds what is particular to each type: how it is
recorded, which arguments are written positionally, which are compared as one whole value, and
what a well-formed record looks like. Nothing read from a record is evaluated: a check only
builds a `datetime` or a `Decimal` from typed fields to confirm they are in range.
"""

from __future__ import annotations

import json
import keyword
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path as FilePath
from pathlib import PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath
from zoneinfo import ZoneInfo

from xpt.path import Attr, Path
from xpt.unsupported import UnsupportedValue, type_name
from xpt.value import Bool, Dataclass, Float, Int, InvalidValue, Null, Ref, Str, Value, to_json

Recurse = Callable[[object, Path], Value]

UTC = "datetime:timezone.utc"
TIMEZONE = "datetime:timezone"
DECIMAL = "decimal:Decimal"
UUID = "uuid:UUID"
ZONEINFO = "zoneinfo:ZoneInfo"
PATH = "pathlib:Path"
PURE_POSIX = "pathlib:PurePosixPath"
PURE_WINDOWS = "pathlib:PureWindowsPath"

_FIELDS: dict[type, tuple[str, tuple[str, ...]]] = {
    datetime: (
        "datetime:datetime",
        ("year", "month", "day", "hour", "minute", "second", "microsecond"),
    ),
    date: ("datetime:date", ("year", "month", "day")),
    time: ("datetime:time", ("hour", "minute", "second", "microsecond")),
    timedelta: ("datetime:timedelta", ("days", "seconds", "microseconds")),
}

TIMESTAMPS = frozenset(type_id for type_id, _ in _FIELDS.values())
"""Dates, times, and durations, which the flaky report calls timestamps."""

WHOLE = frozenset({TIMEZONE, DECIMAL, UUID, ZONEINFO, PATH, PURE_POSIX, PURE_WINDOWS})
"""Types a diff compares as one value, because their fields mean little on their own."""

IMPORTS = TIMESTAMPS | WHOLE | {UTC}
"""Names accept may import from the standard library when the test file binds none of them."""

_CONCRETE = (FilePath, PosixPath, WindowsPath)
_PURE: dict[type, str] = {PurePosixPath: PURE_POSIX, PureWindowsPath: PURE_WINDOWS}


@dataclass(frozen=True, slots=True)
class Signature:
    """How a constructor call is written: `positional` fields in order, the first `required`
    of them always, and the rest only up to the last one that is not 0. A field in `zeros` is
    a keyword left out when it is 0. Every other field is a keyword."""

    positional: tuple[str, ...] = ()
    required: int = 0
    zeros: frozenset[str] = frozenset()


SIGNATURES: Mapping[str, Signature] = {
    "datetime:datetime": Signature(_FIELDS[datetime][1], 3),
    "datetime:date": Signature(_FIELDS[date][1], 3),
    "datetime:time": Signature(_FIELDS[time][1], 0),
    "datetime:timedelta": Signature(zeros=frozenset(_FIELDS[timedelta][1])),
}

_ZERO = Int(0)


def arguments(type_id: str, fields: tuple[tuple[str, Value], ...]) -> list[tuple[str, str, Value]]:
    """The fields of a constructor call to write, as (label, field, value): the label is ""
    for a positional argument and `name=` for a keyword."""
    sig = SIGNATURES.get(type_id)
    named = dict(fields)
    if sig is None or not all(n in named for n in sig.positional):
        return [(name and f"{name}=", name, v) for name, v in fields]
    count = len(sig.positional)
    while count > sig.required and named[sig.positional[count - 1]] == _ZERO:
        count -= 1
    written = [("", n, named[n]) for n in sig.positional[:count]]
    for name, v in fields:
        if name in sig.positional or (name in sig.zeros and v == _ZERO):
            continue
        written.append((f"{name}=", name, v))
    return written


def whole(value: Dataclass) -> bool:
    """Whether a diff compares this call as one value: a type in `WHOLE`, or a call whose only
    argument is one positional scalar, such as `Perm(3)` for a combination of flags."""
    if value.type in WHOLE:
        return True
    match value.fields:
        case (("", Str() | Int() | Float() | Bool() | Null()),):
            return True
    return False


def record(obj: object, path: Path, recurse: Recurse) -> Value | None:
    """`obj` as a value tree when it is one of the types this module knows, else None."""
    kind = type(obj)
    if found := _FIELDS.get(kind):
        type_id, names = found
        fields: list[tuple[str, Value]] = [(n, Int(getattr(obj, n))) for n in names]
        if isinstance(obj, datetime | time):
            if obj.tzinfo is not None:
                fields.append(("tzinfo", recurse(obj.tzinfo, (*path, Attr("tzinfo")))))
            if obj.fold:
                fields.append(("fold", Int(1)))
        return Dataclass(type_id, tuple(fields))
    if isinstance(obj, timezone) and kind is timezone:
        return _timezone(obj, path, recurse)
    if kind is uuid.UUID:
        return _text(UUID, str(obj))
    if kind is Decimal:
        return _text(DECIMAL, str(obj))
    if isinstance(obj, PurePath) and kind in _CONCRETE:
        return _text(PATH, obj.as_posix())
    if isinstance(obj, PurePath) and kind in _PURE:
        return _text(_PURE[kind], obj.as_posix())
    if isinstance(obj, ZoneInfo) and kind is ZoneInfo:
        if obj.key is None:
            raise UnsupportedValue(path, "is a zoneinfo.ZoneInfo read from a file, with no key")
        return _text(ZONEINFO, obj.key)
    if isinstance(obj, Enum):
        return _member(obj, path, recurse)
    return None


def _text(type_id: str, text: str) -> Dataclass:
    return Dataclass(type_id, (("", Str(text)),))


def _timezone(tz: timezone, path: Path, recurse: Recurse) -> Value:
    offset = tz.utcoffset(None)
    name = tz.tzname(None)
    plain = timezone(offset).tzname(None)
    if offset == timedelta(0) and name == plain:
        return Ref(UTC)
    fields: list[tuple[str, Value]] = [("", recurse(offset, path))]
    if name != plain:
        fields.append(("name", Str(name)))
    return Dataclass(TIMEZONE, tuple(fields))


def _member(member: Enum, path: Path, recurse: Recurse) -> Value:
    """`Color.RED` as a reference, or `Perm(3)` for a member without a name of its own, such
    as a combination of flags."""
    cls = type(member)
    name = member._name_
    if (
        isinstance(name, str)
        and cls.__members__.get(name) is member
        and name.isidentifier()
        and not keyword.iskeyword(name)
    ):
        return Ref(f"{type_name(cls)}.{name}")
    return Dataclass(type_name(cls), (("", recurse(member._value_, path)),))


def decimal_key(text: str) -> object:
    """A hashable stand-in for a recorded Decimal, equal when the decimals compare equal:
    `Decimal("1.10")` and `Decimal("1.1")` are one number. NaN equals NaN of the same kind."""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return ("decimal", text)
    if number.is_nan():
        return ("decimal", "snan" if number.is_snan() else "nan")
    return ("decimal", number)


def check(type_id: str, fields: tuple[tuple[str, Value], ...]) -> None:
    """Raise InvalidValue when a record of a type this module knows is malformed."""
    rule = _CHECKS.get(type_id)
    if rule is None:
        return
    problem = rule(dict(fields))
    if problem:
        shown = json.dumps(to_json(Dataclass(type_id, fields)))
        raise InvalidValue(f"malformed {type_id} value {shown}: {problem}")


def _ints(cls: type) -> Callable[[dict[str, Value]], str]:
    _, names = _FIELDS[cls]
    zoned = cls in (datetime, time)
    allowed = set(names) | ({"tzinfo", "fold"} if zoned else set())

    def rule(fields: dict[str, Value]) -> str:
        if set(names) - set(fields) or set(fields) - allowed:
            return f"the fields are {', '.join(names)}" + (
                ", and tzinfo and fold when set" if zoned else ""
            )
        ints = {n: fields[n] for n in names}
        if not all(isinstance(v, Int) for v in ints.values()):
            return "each field is an integer"
        tz = fields.get("tzinfo")
        if tz is not None and not isinstance(tz, Ref | Dataclass):
            return "tzinfo is a timezone, left out when there is none"
        if fields.get("fold", Int(1)) != Int(1):
            return "fold is 1, left out when it is 0"
        given = tuple(v.v for v in ints.values() if isinstance(v, Int))
        try:
            built = cls(**dict(zip(names, given, strict=True)))
        except (ValueError, OverflowError) as error:
            return str(error)
        if tuple(getattr(built, n) for n in names) != given:
            return "the fields are not normalized"
        return ""

    return rule


def _text_rule(parse: Callable[[str], str]) -> Callable[[dict[str, Value]], str]:
    """A record whose one positional field is a string that `parse` gives back unchanged."""

    def rule(fields: dict[str, Value]) -> str:
        text = fields.get("")
        if set(fields) != {""} or not isinstance(text, Str):
            return 'the one field is "", a string'
        try:
            again = parse(text.v)
        except (ValueError, ArithmeticError) as error:
            return str(error) or "it does not parse"
        return "" if again == text.v else f"it is written {json.dumps(again)}"

    return rule


def _timezone_rule(fields: dict[str, Value]) -> str:
    offset, name = fields.get(""), fields.get("name", Str(""))
    if set(fields) - {"", "name"} or not isinstance(name, Str):
        return 'the fields are "", a timedelta, and name, a string, when set'
    if not (isinstance(offset, Dataclass) and offset.type == "datetime:timedelta"):
        return 'field "" is a timedelta'
    parts = {n: v.v for n, v in offset.fields if isinstance(v, Int)}
    try:
        timezone(timedelta(**parts))
    except (TypeError, ValueError, OverflowError) as error:
        return str(error)
    return ""


def _zone_key(text: str) -> str:
    if not text:
        raise ValueError("the key is empty")
    return text


def _any_path(text: str) -> str:
    """A path as `Path` writes it on any system: as POSIX or Windows spells it with slashes."""
    windows = PureWindowsPath(text).as_posix()
    return text if text == windows else PurePosixPath(text).as_posix()


_CHECKS: dict[str, Callable[[dict[str, Value]], str]] = {
    "datetime:datetime": _ints(datetime),
    "datetime:date": _ints(date),
    "datetime:time": _ints(time),
    "datetime:timedelta": _ints(timedelta),
    TIMEZONE: _timezone_rule,
    UUID: _text_rule(lambda s: str(uuid.UUID(s))),
    DECIMAL: _text_rule(lambda s: str(Decimal(s))),
    ZONEINFO: _text_rule(_zone_key),
    PATH: _text_rule(_any_path),
    PURE_POSIX: _text_rule(lambda s: PurePosixPath(s).as_posix()),
    PURE_WINDOWS: _text_rule(lambda s: PureWindowsPath(s).as_posix()),
}
