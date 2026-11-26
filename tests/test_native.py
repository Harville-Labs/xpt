import ast
import json
import shutil
import subprocess
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal
from enum import Enum, Flag, IntEnum, StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_rewrite import rewritten, target

from xpt import expect
from xpt.diff import Change, diff
from xpt.equal import equal
from xpt.expect import ExpectationFailed
from xpt.flaky import paths
from xpt.path import Attr, Index, Key
from xpt.rewrite import rewrite
from xpt.serialize import UnsupportedValue, serialize
from xpt.source import one_line, to_source
from xpt.value import Dataclass, Int, InvalidValue, Null, Ref, Str, from_json, to_json


class Color(Enum):
    RED = 1
    BLUE = 2


class Level(IntEnum):
    LOW = 1


class Mode(StrEnum):
    READ = "r"


class Perm(Flag):
    R = 1
    W = 2


def zone(key: str) -> ZoneInfo:
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        pytest.skip(f"no time zone data for {key}")


NAMESPACE: dict[str, object] = {
    "datetime": datetime,
    "date": date,
    "time": time,
    "timedelta": timedelta,
    "timezone": timezone,
    "UUID": UUID,
    "Decimal": Decimal,
    "Path": Path,
    "PurePosixPath": PurePosixPath,
    "PureWindowsPath": PureWindowsPath,
    "ZoneInfo": ZoneInfo,
    "Color": Color,
    "Level": Level,
    "Perm": Perm,
    "Mode": Mode,
}

zones = st.sampled_from(
    [
        None,
        UTC,
        timezone(timedelta(hours=5, minutes=30)),
        timezone(-timedelta(hours=3), "X"),
    ]
)
natives = st.one_of(
    st.datetimes(timezones=zones),
    st.dates(),
    st.times(timezones=zones),
    st.timedeltas(),
    st.uuids(),
    st.decimals(),
    st.lists(st.sampled_from(["a", "b c", "ü", ".."]), max_size=3).map(lambda p: Path(*p)),
    st.sampled_from([PurePosixPath("/a/b"), PureWindowsPath("C:\\a\\b"), PureWindowsPath("x")]),
    st.sampled_from([Color.RED, Level.LOW, Perm.R, Perm.R | Perm.W, Perm(0)]),
)
nested = st.one_of(
    natives,
    st.lists(natives, max_size=3),
    st.dictionaries(st.text(max_size=3), natives, max_size=3),
)


def evaluate(src: str) -> object:
    return eval(compile(ast.parse(src, mode="eval"), "<test>", "eval"), dict(NAMESPACE))


def roundtrip(obj: object):
    return from_json(json.loads(json.dumps(to_json(serialize(obj)))))


@pytest.mark.parametrize(
    ("obj", "written"),
    [
        (datetime(2000, 1, 1), "datetime(2000, 1, 1)"),
        (datetime(2000, 1, 1, 0, 0, 5), "datetime(2000, 1, 1, 0, 0, 5)"),
        (
            datetime(2000, 1, 1, 12, tzinfo=UTC),
            "datetime(2000, 1, 1, 12, tzinfo=timezone.utc)",
        ),
        (datetime(2000, 1, 1, fold=1), "datetime(2000, 1, 1, fold=1)"),
        (date(2026, 11, 26), "date(2026, 11, 26)"),
        (time(), "time()"),
        (
            time(9, 30, tzinfo=timezone(timedelta(hours=1))),
            "time(9, 30, tzinfo=timezone(timedelta(seconds=3600)))",
        ),
        (timedelta(), "timedelta()"),
        (timedelta(days=2, microseconds=7), "timedelta(days=2, microseconds=7)"),
        (
            timezone(timedelta(hours=-5), "EST"),
            'timezone(timedelta(days=-1, seconds=68400), name="EST")',
        ),
        (UUID(int=1), 'UUID("00000000-0000-0000-0000-000000000001")'),
        (Decimal("1.10"), 'Decimal("1.10")'),
        (Decimal("1E+3"), 'Decimal("1E+3")'),
        (Decimal("-0"), 'Decimal("-0")'),
        (Decimal("NaN"), 'Decimal("NaN")'),
        (Path("a") / "b", 'Path("a/b")'),
        (PurePosixPath("/etc/hosts"), 'PurePosixPath("/etc/hosts")'),
        (PureWindowsPath("C:\\Users\\a"), 'PureWindowsPath("C:/Users/a")'),
        (Color.RED, "Color.RED"),
        (Level.LOW, "Level.LOW"),
        (Perm.W, "Perm.W"),
        (Mode.READ, "Mode.READ"),
        (Perm.R | Perm.W, "Perm(3)"),
    ],
)
def test_each_type_is_written_as_the_call_that_rebuilds_it(obj: object, written: str):
    assert one_line(serialize(obj)) == written
    rebuilt = evaluate(written)
    assert rebuilt == obj or (isinstance(obj, Decimal) and obj.is_nan())
    assert str(rebuilt) == str(obj)


def test_a_zoneinfo_is_written_by_its_key():
    london = zone("Europe/London")
    assert one_line(serialize(datetime(2000, 6, 1, tzinfo=london))) == (
        'datetime(2000, 6, 1, tzinfo=ZoneInfo("Europe/London"))'
    )


def test_the_recorded_shapes():
    assert to_json(serialize(datetime(2000, 1, 2, 3, tzinfo=UTC))) == {
        "datetime:datetime": {
            "year": 2000,
            "month": 1,
            "day": 2,
            "hour": 3,
            "minute": 0,
            "second": 0,
            "microsecond": 0,
            "tzinfo": {"ref": "datetime:timezone.utc"},
        }
    }
    assert to_json(serialize(timedelta(seconds=1))) == {
        "datetime:timedelta": {"days": 0, "seconds": 1, "microseconds": 0}
    }
    assert to_json(serialize(Decimal("1.10"))) == {"decimal:Decimal": {"": "1.10"}}
    assert to_json(serialize(UUID(int=2))) == {
        "uuid:UUID": {"": "00000000-0000-0000-0000-000000000002"}
    }
    assert to_json(serialize(Path("a/b"))) == {"pathlib:Path": {"": "a/b"}}
    assert to_json(serialize(Color.BLUE)) == {"ref": "test_native:Color.BLUE"}
    assert to_json(serialize(Perm(3))) == {"test_native:Perm": {"": 3}}


def test_a_decimal_keeps_its_exponent():
    assert one_line(serialize(Decimal("1.10"))) != one_line(serialize(Decimal("1.1")))


@given(nested)
def test_every_native_value_roundtrips_through_json(obj: object):
    assert roundtrip(obj) == serialize(obj)


@given(nested)
def test_printed_source_evaluates_to_the_same_value(obj: object):
    assert serialize(evaluate(one_line(serialize(obj)))) == serialize(obj)


NAMES = {
    "datetime:datetime": "datetime",
    "datetime:timezone": "timezone",
    "datetime:timedelta": "timedelta",
    "datetime:timezone.utc": "timezone.utc",
    "uuid:UUID": "UUID",
    "decimal:Decimal": "Decimal",
}


def test_ruff_format_leaves_a_long_native_value_unchanged():
    ruff = shutil.which("ruff")
    assert ruff, "ruff must be installed in the dev environment"
    value = {
        "created": datetime(2000, 1, 1, 12, 30, 45, 123456, tzinfo=timezone(timedelta(hours=2))),
        "ids": [UUID(int=n) for n in range(3)],
        "total": Decimal("12345.6700"),
    }
    src = "x = " + to_source(serialize(value), 0, column=4, names=NAMES) + "\n"
    assert "\n" in src
    done = subprocess.run(
        [ruff, "format", "--isolated", "--line-length", "100", "--diff", "-"],
        input=src,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert done.stdout == ""


def test_equal_values_pass():
    expect(datetime(2000, 1, 1, tzinfo=UTC), datetime(2000, 1, 1, tzinfo=UTC))
    expect({"price": Decimal("1.10")}, {"price": Decimal("1.1")})
    expect(Decimal("NaN"), Decimal("NaN"))
    expect([Color.RED, Perm.R | Perm.W], [Color.RED, Perm(3)])
    expect(Path("a/b"), Path("a") / "b")


def test_decimal_nan_equals_only_a_nan_of_the_same_kind():
    assert equal(Decimal("NaN"), Decimal("-NaN"))
    assert not equal(Decimal("NaN"), Decimal("sNaN"))


def d(expected: object, actual: object):
    return diff(serialize(expected), serialize(actual)).changes


def test_dates_and_times_diff_by_field():
    assert d({"at": datetime(2000, 1, 1)}, {"at": datetime(2000, 1, 1, 12)}) == (
        Change((Key(Str("at")), Attr("hour")), "replace", Int(0), Int(12)),
    )
    assert d(timedelta(seconds=1), timedelta(seconds=2)) == (
        Change((Attr("seconds"),), "replace", Int(1), Int(2)),
    )
    [change] = d(datetime(2000, 1, 1), datetime(2000, 1, 1, tzinfo=UTC))
    assert (change.path, change.op, change.actual) == (
        (Attr("tzinfo"),),
        "add",
        Ref("datetime:timezone.utc"),
    )


def test_uuids_decimals_paths_and_members_diff_as_whole_values():
    for a, b in [
        (UUID(int=1), UUID(int=2)),
        (Decimal("1.10"), Decimal("1.2")),
        (Path("a"), Path("b")),
        (timezone(timedelta(hours=1)), timezone(timedelta(hours=2))),
        (Color.RED, Color.BLUE),
        (Perm(1), Perm(3)),
    ]:
        assert d([a], [b]) == (Change((Index(0),), "replace", serialize(a), serialize(b)),)


def test_equal_decimals_written_differently_are_unchanged_in_a_diff():
    assert d({"a": Decimal("1.1"), "b": 1}, {"a": Decimal("1.10"), "b": 2}) == (
        Change((Key(Str("b")),), "replace", Int(1), Int(2)),
    )


def test_a_mismatch_names_the_field_that_changed():
    with pytest.raises(ExpectationFailed) as info:
        expect({"at": datetime(2000, 1, 1, 9)}, {"at": datetime(2000, 1, 1, 8)})
    assert str(info.value).splitlines()[1] == '  value["at"].hour: 8 -> 9'
    with pytest.raises(ExpectationFailed) as info:
        expect(UUID(int=2), UUID(int=1))
    assert str(info.value).splitlines()[1] == (
        '  value: UUID("00000000-0000-0000-0000-000000000001") -> '
        'UUID("00000000-0000-0000-0000-000000000002")'
    )


def test_flaky_report_calls_a_changing_datetime_a_timestamp_and_a_uuid_a_uuid():
    runs = ("r1", "r2")
    first = serialize({"at": datetime(2000, 1, 1, 0, 0, 1), "id": UUID(int=1)})
    second = serialize({"at": datetime(2000, 1, 1, 0, 0, 2), "id": UUID(int=2)})
    found = {v.kind: v for v in paths([first, second], runs)}
    assert found["timestamp"].path == (Key(Str("at")),)
    assert found["timestamp"].suggestion == """ignore=['value["at"]']"""
    assert found["uuid"].path == (Key(Str("id")),)


class Fixed(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "Fixed"


def test_a_time_zone_xpt_does_not_know_is_unsupported_at_its_path():
    with pytest.raises(UnsupportedValue, match=r"value\.tzinfo is a test_native:Fixed"):
        serialize(datetime(2000, 1, 1, tzinfo=Fixed()))


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({"datetime:datetime": {"year": 2000, "month": 1}}, "the fields are year"),
        (
            {"datetime:date": {"year": 2000, "month": 13, "day": 1}},
            "month must be in 1..12",
        ),
        ({"datetime:date": {"year": "2000", "month": 1, "day": 1}}, "each field is an integer"),
        (
            {"datetime:timedelta": {"days": 0, "seconds": 90000, "microseconds": 0}},
            "not normalized",
        ),
        (
            {
                "datetime:time": {
                    "hour": 0,
                    "minute": 0,
                    "second": 0,
                    "microsecond": 0,
                    "tzinfo": "UTC",
                }
            },
            "tzinfo is a timezone",
        ),
        (
            {
                "datetime:time": {
                    "hour": 0,
                    "minute": 0,
                    "second": 0,
                    "microsecond": 0,
                    "fold": 0,
                }
            },
            "fold is 1",
        ),
        ({"datetime:timezone": {"": 1}}, 'field "" is a timedelta'),
        (
            {
                "datetime:timezone": {
                    "": {"datetime:timedelta": {"days": 1, "seconds": 0, "microseconds": 0}}
                }
            },
            "timedelta",
        ),
        ({"uuid:UUID": {"": "nope"}}, "badly formed"),
        ({"uuid:UUID": {"": "0000000000000000000000000000000A"}}, "it is written"),
        ({"decimal:Decimal": {"": "1.1.1"}}, "malformed decimal:Decimal"),
        ({"decimal:Decimal": {"": "1e3"}}, 'it is written "1E\\+3"'),
        ({"decimal:Decimal": {"value": "1"}}, 'the one field is ""'),
        ({"pathlib:PurePosixPath": {"": "a//b"}}, 'it is written "a/b"'),
        ({"pathlib:Path": {"": ""}}, "it is written"),
        ({"zoneinfo:ZoneInfo": {"": ""}}, "the key is empty"),
    ],
)
def test_a_malformed_record_is_refused(data: object, problem: str):
    with pytest.raises(InvalidValue, match=problem):
        from_json(data)


def target_source(source: str, obj: object) -> str:
    return rewritten(source, serialize(obj))


def test_accept_adds_the_standard_library_import_the_file_needs():
    source = '"""Doc."""\n\nimport os\n\n\ndef test_it():\n    expect(make())\n'
    value = [datetime(2000, 1, 1, tzinfo=UTC), UUID(int=1)]
    assert target_source(source, value) == (
        '"""Doc."""\n\nimport os\nfrom datetime import datetime, timezone\nfrom uuid import UUID\n'
        "\n\ndef test_it():\n"
        "    expect(\n"
        "        make(),\n"
        "        [datetime(2000, 1, 1, tzinfo=timezone.utc), "
        'UUID("00000000-0000-0000-0000-000000000001")],\n'
        "    )\n"
    )


def test_accept_reuses_a_module_import_or_an_alias():
    source = "import datetime as dt\n\ndef test_it():\n    expect(make())\n"
    assert target_source(source, datetime(2000, 1, 1)).endswith(
        "    expect(make(), dt.datetime(2000, 1, 1))\n"
    )
    source = "from decimal import Decimal as D\n\ndef test_it():\n    expect(make())\n"
    assert target_source(source, Decimal("2.50")).endswith('    expect(make(), D("2.50"))\n')


def test_an_import_that_would_shadow_a_name_the_file_uses_is_not_added():
    source = "def test_it():\n    UUID = 1\n    expect(make())\n"
    plan = rewrite("test_it.py", source.encode("utf-8"), [target(source, serialize(UUID(int=1)))])
    [outcome] = plan.outcomes
    assert (outcome.status, outcome.reason) == (
        "skipped",
        "test_it.py:3 needs the name UUID from uuid, which test_it.py does not import at module "
        "level, and adding the import would shadow a name test_it.py already uses. "
        "Import uuid or UUID and accept again.",
    )
    assert plan.data == source.encode("utf-8")


def test_an_enum_member_is_written_by_its_class_and_never_imported():
    source = "from test_native import Color\n\ndef test_it():\n    expect(make())\n"
    assert target_source(source, Color.RED).endswith("    expect(make(), Color.RED)\n")
    source = "def test_it():\n    expect(make())\n"
    plan = rewrite("test_it.py", source.encode("utf-8"), [target(source, serialize(Color.RED))])
    [outcome] = plan.outcomes
    assert outcome.status == "skipped"
    assert "needs the name Color.RED from test_native" in outcome.reason


def test_a_null_tzinfo_is_never_recorded():
    value = serialize(time(1))
    assert isinstance(value, Dataclass)
    assert Null() not in dict(value.fields).values()


APP = """\
from datetime import datetime
from decimal import Decimal
from uuid import uuid4


def create_order():
    return {"id": uuid4(), "created_at": datetime.now(), "total": Decimal("9.90")}
"""

TEST = """\
from app import create_order

from xpt import expect


def test_create(xpt_deterministic):
    expect(create_order())
"""


def test_a_pinned_datetime_and_uuid4_are_expected_directly(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)
    monkeypatch.delenv("XPT_REPORT", raising=False)
    pytester.makepyfile(app=APP, test_it=TEST)
    pytester.makeini("[pytest]\npythonpath = .\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=1)
    accepted = pytester.run("xpt", "accept", "--all")
    assert accepted.ret == 0, accepted.outlines
    source = (pytester.path / "test_it.py").read_text(encoding="utf-8")
    assert source == (
        "from app import create_order\n"
        "\n"
        "from xpt import expect\n"
        "from datetime import datetime\n"
        "from decimal import Decimal\n"
        "from uuid import UUID\n"
        "\n"
        "\n"
        "def test_create(xpt_deterministic):\n"
        "    expect(\n"
        "        create_order(),\n"
        "        {\n"
        '            "id": UUID("00000000-0000-4000-8000-000000000001"),\n'
        '            "created_at": datetime(2000, 1, 1),\n'
        '            "total": Decimal("9.90"),\n'
        "        },\n"
        "    )"
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)
