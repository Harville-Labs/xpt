import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from xpt.cli import main
from xpt.record import Observation, Recorder
from xpt.site import CallSite, Unlocatable
from xpt.value import Dict, Int, Str, Value

RUN = "20260101T000000-aaaa"
NOT_TRACED = {"status": "no_trace", "since": None, "functions": [], "stale": [], "note": ""}
OLD = "20251231T000000-bbbb"


@dataclass(frozen=True, slots=True)
class Ran:
    code: int
    out: str
    err: str

    @property
    def json(self) -> Any:
        return json.loads(self.out)


def xpt(capsys: pytest.CaptureFixture[str], *argv: str) -> Ran:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return Ran(code, captured.out, captured.err)


def recorded(root: Path, observations: Iterable[Observation], run: str = RUN) -> None:
    recorder = Recorder(root, run, "main", enabled=True, managed=True)
    for o in observations:
        recorder.record(o)
    recorder.finish()


def site(path: str, qualname: str, line: int, sha: str = "ab" * 32) -> CallSite:
    return CallSite(path, qualname, "expect", 0, sha, line)


def observed(
    test: str,
    where: CallSite | Unlocatable,
    actual: Value | None,
    expected: Value | None = None,
    detail: str = "",
) -> Observation:
    status = "missing" if expected is None else "mismatch"
    return Observation(RUN, test, where, status, expected, actual, detail)


def op(name: str) -> Dict:
    return Dict(((Str("op"), Str(name)), (Str("n"), Int(1))))


MISMATCH = site("tests/test_a.py", "test_a", 7)
MISSING = site("tests/test_a.py", "test_b", 12, sha="")
CONFLICT = site("tests/test_b.py", "test_c", 3, sha="")
LOST = Unlocatable("expect() was called from code with no source file.", "tests/test_c.py", 5)

EVERY_KIND = [
    observed("tests/test_a.py::test_a[1]", MISMATCH, op("sub"), op("add")),
    observed("tests/test_a.py::test_a[2]", MISMATCH, op("sub"), op("add")),
    observed("tests/test_a.py::test_b", MISSING, Str("héllo")),
    observed("tests/test_b.py::test_c[1]", CONFLICT, Int(1)),
    observed("tests/test_b.py::test_c[2]", CONFLICT, Int(2)),
    observed("tests/test_c.py::test_d", LOST, Int(3)),
]
