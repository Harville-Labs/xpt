"""Pin the clock, `random`, and `uuid` for one test, so the values it records are the same on
every run.

`deterministic(test_id)` is a context manager that works with or without pytest. The pytest
plugin wraps it as the `xpt_deterministic` fixture and the `--xpt-deterministic` option.
Only the standard library is patched, and everything is restored on exit.
"""

from __future__ import annotations

import datetime as _datetime
import os
import random
import sys
import sysconfig
import time
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xpt.sources import SKIPPED_DIRS

START = _datetime.datetime(2000, 1, 1, tzinfo=_datetime.UTC)
"""The instant the pinned clock starts at."""

_START_NS = int(START.timestamp()) * 1_000_000_000
_NS = 1_000_000_000
_REAL_DATETIME = _datetime.datetime
_REAL_DATE = _datetime.date
_REAL_TIME = time.time
_REAL_TIME_NS = time.time_ns
_REAL_UUID1 = uuid.uuid1
_REAL_UUID4 = uuid.uuid4
_UNPATCHED = frozenset({"xpt", "_pytest", "pytest", "pluggy", "xdist"})
_INSTALLED = SKIPPED_DIRS | {"dist-packages"}


@dataclass(slots=True)
class Pinned:
    """The pinned state of one test. Its clock moves only through `advance`."""

    test_id: str
    ns: int = _START_NS
    counts: dict[int, int] = field(default_factory=dict)

    def advance(self, seconds: float | _datetime.timedelta) -> None:
        """Move the clock forward by `seconds`, a number or a timedelta."""
        if isinstance(seconds, _datetime.timedelta):
            ns = (seconds.days * 86_400 + seconds.seconds) * _NS + seconds.microseconds * 1_000
        else:
            ns = round(seconds * _NS)
        if ns < 0:
            raise ValueError("The pinned clock only moves forward. Pass a positive duration.")
        self.ns += ns

    def now(self) -> _datetime.datetime:
        """The pinned instant, as an aware UTC datetime."""
        return START + _datetime.timedelta(microseconds=(self.ns - _START_NS) // 1_000)

    def next_uuid(self, version: int) -> uuid.UUID:
        """The next UUID of `version`, counting from 1 for each version."""
        n = self.counts.get(version, 0) + 1
        self.counts[version] = n
        return uuid.UUID(int=n, version=version)


_active: Pinned | None = None


def active() -> Pinned | None:
    """The pinned state in force, None outside `deterministic`."""
    return _active


def _time() -> float:
    return _REAL_TIME() if _active is None else _active.ns / _NS


def _time_ns() -> int:
    return _REAL_TIME_NS() if _active is None else _active.ns


def _uuid1(node: int | None = None, clock_seq: int | None = None) -> uuid.UUID:
    return _REAL_UUID1(node, clock_seq) if _active is None else _active.next_uuid(1)


def _uuid4() -> uuid.UUID:
    return _REAL_UUID4() if _active is None else _active.next_uuid(4)


class _Stands(type):
    """A metaclass that makes the stand-in class accept the real class's instances, so
    `isinstance(value, datetime)` still holds for values made before the patch or by C code."""

    _real: type

    def __instancecheck__(cls, instance: object) -> bool:
        return isinstance(instance, cls._real)

    def __subclasscheck__(cls, subclass: type) -> bool:
        return issubclass(subclass, cls._real)


class _Datetime(_REAL_DATETIME, metaclass=_Stands):
    """`datetime.datetime` while pinned. It builds real datetimes, so recorded values and
    `type(value)` are unchanged, and reads the pinned clock."""

    _real = _REAL_DATETIME

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _REAL_DATETIME(*args, **kwargs)

    @classmethod
    def now(cls, tz: _datetime.tzinfo | None = None) -> Any:
        if _active is None:
            return _REAL_DATETIME.now(tz)
        instant = _active.now()
        return instant.replace(tzinfo=None) if tz is None else instant.astimezone(tz)

    @classmethod
    def utcnow(cls) -> Any:
        if _active is None:
            return _REAL_DATETIME.now(_datetime.UTC).replace(tzinfo=None)
        return _active.now().replace(tzinfo=None)

    @classmethod
    def today(cls) -> Any:
        return cls.now()


class _Date(_REAL_DATE, metaclass=_Stands):
    """`datetime.date` while pinned, building real dates."""

    _real = _REAL_DATE

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _REAL_DATE(*args, **kwargs)

    @classmethod
    def today(cls) -> Any:
        return _REAL_DATE.today() if _active is None else _active.now().date()


_PATCHES: tuple[tuple[Any, str, object, object], ...] = (
    (time, "time", _REAL_TIME, _time),
    (time, "time_ns", _REAL_TIME_NS, _time_ns),
    (_datetime, "datetime", _REAL_DATETIME, _Datetime),
    (_datetime, "date", _REAL_DATE, _Date),
    (uuid, "uuid1", _REAL_UUID1, _uuid1),
    (uuid, "uuid4", _REAL_UUID4, _uuid4),
)
_STAND_INS = {id(real): fake for _, _, real, fake in _PATCHES}


@contextmanager
def deterministic(test_id: str, *, root: Path | None = None) -> Generator[Pinned]:
    """Pin `random`, `uuid.uuid1`, `uuid.uuid4`, and the clock until the block exits.

    `random` is seeded from `test_id`, so each test has its own fixed sequence. `uuid4` counts
    up from `00000000-0000-4000-8000-000000000001`, and `uuid1` from
    `00000000-0000-1000-8000-000000000001`. `time.time`, `time.time_ns`,
    `datetime.datetime.now`, `utcnow`, `today`, and `datetime.date.today` read 2000-01-01
    00:00:00 UTC until `Pinned.advance` moves them. Naive results are in UTC, so a test reads
    the same in every time zone.

    Modules under `root`, the current directory by default, that imported one of these names
    directly, as in `from datetime import datetime`, are rebound too. Installed packages, the
    standard library, and C code keep what they imported. Everything is restored on exit,
    also when the block raises.
    """
    global _active
    if _active is not None:
        raise RuntimeError("deterministic() is already active. Leave the outer block first.")
    undo: list[tuple[Any, str, object]] = []
    state = random.getstate()
    try:
        for owner, name, real, fake in _PATCHES:
            if getattr(owner, name) is real:
                undo.append((owner, name, real))
                setattr(owner, name, fake)
        for module in _project_modules(Path(os.path.abspath(Path.cwd() if root is None else root))):
            for name, value in list(module.items()):
                fake = _STAND_INS.get(id(value))
                if fake is not None:
                    undo.append((module, name, value))
                    module[name] = fake
        random.seed(f"xpt:{test_id}")
        _active = Pinned(test_id)
        yield _active
    finally:
        _active = None
        for owner, name, original in reversed(undo):
            if isinstance(owner, dict):
                owner[name] = original
            else:
                setattr(owner, name, original)
        random.setstate(state)


def _project_modules(root: Path) -> Iterator[dict[str, Any]]:
    """The namespace of each imported module whose file is under `root`, outside installed
    packages, hidden directories, the standard library, and xpt's own test plumbing."""
    stdlib = [Path(p) for k in ("stdlib", "platstdlib") if (p := sysconfig.get_path(k))]
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        namespace = getattr(module, "__dict__", None)
        if not isinstance(file, str) or not isinstance(namespace, dict):
            continue
        if name.partition(".")[0] in _UNPATCHED:
            continue
        path = Path(os.path.abspath(file))
        if not path.is_relative_to(root) or any(path.is_relative_to(s) for s in stdlib):
            continue
        parts = path.relative_to(root).parts[:-1]
        if any(p.startswith(".") or p in _INSTALLED for p in parts):
            continue
        yield namespace
