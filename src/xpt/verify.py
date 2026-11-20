from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from xpt.rewrite import Outcome


@dataclass(frozen=True, slots=True)
class Rerun:
    """The accepted tests that `xpt accept --verify` ran again, and how pytest exited.

    `code` is None when there was nothing to rerun, so pytest never started.
    """

    tests: tuple[str, ...]
    code: int | None
    output: str

    @property
    def passed(self) -> bool | None:
        return None if self.code is None else self.code == 0


def accepted_tests(outcomes: Iterable[Outcome]) -> tuple[str, ...]:
    """The pytest node ids that observed a call site whose source now holds the actual value."""
    written = ("accepted", "already_accepted")
    return tuple(sorted({t for o in outcomes if o.status in written for t in o.group.tests}))


def rerun(root: Path, tests: tuple[str, ...]) -> Rerun:
    """Run `tests` with this interpreter's pytest from the project root."""
    if not tests:
        return Rerun((), None, "")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", *tests],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    return Rerun(tests, done.returncode, done.stdout)
