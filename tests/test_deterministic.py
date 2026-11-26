import datetime
import importlib
import random
import sys
import time
import unittest
import uuid
from pathlib import Path

import pytest
from cli_support import xpt

from xpt.deterministic import START, active, deterministic
from xpt.record import load_run, run_ids
from xpt.value import to_json

ORIGINALS = (
    time.time,
    time.time_ns,
    datetime.datetime,
    datetime.date,
    uuid.uuid1,
    uuid.uuid4,
)


def originals() -> tuple[object, ...]:
    return (time.time, time.time_ns, datetime.datetime, datetime.date, uuid.uuid1, uuid.uuid4)


def test_the_clock_reads_the_start_until_it_is_advanced(tmp_path: Path):
    with deterministic("t", root=tmp_path) as pinned:
        assert time.time() == 946_684_800.0
        assert time.time_ns() == 946_684_800 * 10**9
        assert datetime.datetime.now() == datetime.datetime(2000, 1, 1)
        assert datetime.datetime.now(datetime.UTC) == START
        utc = datetime.datetime.utcnow()  # ty: ignore[deprecated]
        assert utc == datetime.datetime(2000, 1, 1)
        assert datetime.datetime.today() == datetime.datetime(2000, 1, 1)
        assert datetime.date.today() == datetime.date(2000, 1, 1)
        pinned.advance(90.5)
        pinned.advance(datetime.timedelta(days=1))
        assert datetime.datetime.now() == datetime.datetime(2000, 1, 2, 0, 1, 30, 500_000)
        assert time.time() == 946_684_800 + 86_400 + 90.5
        with pytest.raises(ValueError, match="only moves forward"):
            pinned.advance(-1)


def test_the_stand_in_classes_build_and_accept_real_values(tmp_path: Path):
    before = datetime.datetime(1999, 1, 1)
    with deterministic("t", root=tmp_path):
        made = datetime.datetime(2001, 2, 3, 4, 5)
        assert type(made) is type(before)
        assert type(datetime.datetime.now()) is type(before)
        assert type(datetime.date.today()) is type(before.date())
        assert isinstance(before, datetime.datetime)
        assert isinstance(before, datetime.date)
        assert issubclass(type(before), datetime.datetime)
        assert datetime.datetime.fromisoformat("2001-02-03T04:05") == made


def test_uuids_count_up_and_are_valid(tmp_path: Path):
    with deterministic("t", root=tmp_path):
        first, second = uuid.uuid4(), uuid.uuid4()
        one = uuid.uuid1()
    assert str(first) == "00000000-0000-4000-8000-000000000001"
    assert str(second) == "00000000-0000-4000-8000-000000000002"
    assert str(one) == "00000000-0000-1000-8000-000000000001"
    assert (first.version, first.variant, one.version) == (4, uuid.RFC_4122, 1)


def test_random_is_seeded_from_the_test_id(tmp_path: Path):
    def draw(test_id: str) -> list[int]:
        with deterministic(test_id, root=tmp_path):
            return [random.randrange(1000) for _ in range(5)]

    assert draw("a") == draw("a")
    assert draw("a") != draw("b")


def test_everything_is_restored_on_exit_and_on_an_exception(tmp_path: Path):
    random.seed(7)
    expected = [random.random() for _ in range(3)]
    random.seed(7)
    random.random()
    with pytest.raises(KeyError), deterministic("t", root=tmp_path):
        random.random()
        raise KeyError("boom")
    assert originals() == ORIGINALS
    assert active() is None
    assert [random.random() for _ in range(2)] == expected[1:]
    assert time.time() > 1_700_000_000


def test_it_does_not_nest(tmp_path: Path):
    with (
        deterministic("t", root=tmp_path),
        pytest.raises(RuntimeError, match="already active"),
        deterministic("u", root=tmp_path),
    ):
        pass
    assert originals() == ORIGINALS


def test_project_modules_that_imported_the_names_are_rebound_and_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "clocked.py").write_text(
        "from datetime import datetime\nfrom time import time\nfrom uuid import uuid4\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    clocked = importlib.import_module("clocked")
    monkeypatch.setitem(sys.modules, "clocked", clocked)
    with deterministic("t", root=tmp_path):
        assert clocked.datetime.now() == datetime.datetime(2000, 1, 1)
        assert clocked.time() == 946_684_800.0
        assert str(clocked.uuid4()).endswith("000000000001")
    assert (clocked.datetime, clocked.time, clocked.uuid4) == (
        ORIGINALS[2],
        ORIGINALS[0],
        ORIGINALS[5],
    )


def test_unittest_can_pin_a_test_with_enter_context(tmp_path: Path):
    seen: list[str] = []

    class Case(unittest.TestCase):
        def setUp(self):
            self.pinned = self.enterContext(deterministic(self.id(), root=tmp_path))

        def test_it(self):
            self.pinned.advance(60)
            seen.append(datetime.datetime.now().isoformat())

    result = unittest.TextTestRunner(stream=open(tmp_path / "out", "w")).run(  # noqa: SIM115
        unittest.defaultTestLoader.loadTestsFromTestCase(Case)
    )
    assert result.wasSuccessful()
    assert seen == ["2000-01-01T00:01:00"]
    assert originals() == ORIGINALS


APP = """\
import random
from datetime import datetime
from uuid import uuid4


def create_order():
    return {
        "id": str(uuid4()),
        "created_at": datetime.now().isoformat(),
        "coupon": random.choice(["A", "B", "C", "D", "E", "F", "G", "H"]),
        "lucky": [random.randrange(10**6) for _ in range(3)],
    }
"""

FIXTURE_TEST = """\
from app import create_order

from xpt import expect


def test_create(xpt_deterministic):
    expect(create_order())


def test_later(xpt_deterministic):
    xpt_deterministic.advance(3600)
    expect(create_order()["created_at"], "2000-01-01T01:00:00")
"""

PLAIN_TEST = """\
from app import create_order

from xpt import expect


def test_create():
    expect(create_order())
"""


def run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, seed: str, *args: str
) -> object:
    """Run the suite with `seed` as the hash seed, and return test_create's recorded value."""
    monkeypatch.setenv("PYTHONHASHSEED", seed)
    outcomes = pytester.runpytest_subprocess("-p", "no:cacheprovider", *args).parseoutcomes()
    assert outcomes.get("failed") == 1
    [o] = [o for o in load_run(pytester.path) if o.test.endswith("::test_create")]
    assert o.actual is not None
    return to_json(o.actual)


def test_the_fixture_records_the_same_value_on_two_runs(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    pytester.makepyfile(app=APP, test_it=FIXTURE_TEST)
    pytester.makeini("[pytest]\npythonpath = .\n")
    first = run(pytester, monkeypatch, "1")
    assert run(pytester, monkeypatch, "2") == first
    assert isinstance(first, dict)
    order = first["dict"]
    assert order["id"] == "00000000-0000-4000-8000-000000000001"
    assert order["created_at"] == "2000-01-01T00:00:00"
    assert len(run_ids(pytester.path)) == 2
    assert xpt(capsys, "flaky").code == 0


def test_the_option_pins_every_test_and_restores_after_each(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    pytester.makepyfile(app=APP, test_it=PLAIN_TEST)
    pytester.makeini("[pytest]\npythonpath = .\n")
    plain = run(pytester, monkeypatch, "1")
    first = run(pytester, monkeypatch, "2", "--xpt-deterministic")
    assert run(pytester, monkeypatch, "3", "--xpt-deterministic") == first
    assert plain != first


def test_the_option_records_the_same_value_under_xdist(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("xdist")
    pytester.makepyfile(app=APP, test_it=PLAIN_TEST)
    pytester.makeini("[pytest]\npythonpath = .\n")
    first = run(pytester, monkeypatch, "1", "--xpt-deterministic")
    assert run(pytester, monkeypatch, "2", "--xpt-deterministic", "-n", "2") == first
