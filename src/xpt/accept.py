from __future__ import annotations

import os
import secrets
import shutil
from collections import defaultdict
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from xpt.group import Group
from xpt.record import state_directory
from xpt.rewrite import Outcome, rewrite
from xpt.site import Unlocatable
from xpt.suspect import suspect


@dataclass(frozen=True, slots=True)
class AcceptResult:
    """One outcome per group, in the order given, and the project paths that were rewritten."""

    outcomes: tuple[Outcome, ...]
    changed: tuple[str, ...]


class AcceptError(Exception):
    pass


_FORCE_HINT = " Fix the code under test, or accept it with --force if it is right."


def accept(
    root: Path, groups: Iterable[Group], *, dry_run: bool = False, force: bool = False
) -> AcceptResult:
    """Write each acceptable group's actual value into its source file as the expected value.

    A group is written only when its expected argument's text still matches the run, and,
    unless `force`, when its actual value does not look broken (see `xpt.suspect`). Each
    file is rewritten in one pass to a temporary file and moved into place, under an
    exclusive `.xpt/accept.lock`. A dry run reports the same result and writes nothing.
    """
    given = tuple(groups)
    outcomes: dict[int, Outcome] = {}
    by_path: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(given):
        site = g.site
        if not g.acceptable or isinstance(site, Unlocatable):
            outcomes[i] = Outcome(g, "skipped", g.detail)
        elif not force and (found := suspect(g.expected, g.actuals[0])):
            outcomes[i] = Outcome(g, "suspect", found.reason + _FORCE_HINT)
        else:
            by_path[site.path].append(i)
    changed = []
    with lock(state_directory(root) / "accept.lock"):
        for path, indexes in sorted(by_path.items()):
            done = _accept_file(root, path, [given[i] for i in indexes], dry_run)
            outcomes.update(zip(indexes, done.outcomes, strict=True))
            if done.changed:
                changed.append(path)
    return AcceptResult(tuple(outcomes[i] for i in range(len(given))), tuple(changed))


@dataclass(frozen=True, slots=True)
class Accepted:
    """What `pytest --expect-accept` did after the session: the result for the named groups
    the run had, and the named ids it had no group for."""

    result: AcceptResult
    unknown: tuple[str, ...]


def after_session(root: Path, groups: Sequence[Group]) -> AcceptResult:
    """Accept groups the way `pytest --expect-create` and `--expect-accept` do after the session.

    A held lock skips every group with the lock's message instead of raising, so the session
    still ends with its report.
    """
    if not groups:
        return AcceptResult((), ())
    try:
        return accept(root, groups)
    except AcceptError as error:
        return AcceptResult(tuple(Outcome(g, "skipped", str(error)) for g in groups), ())


@dataclass(frozen=True, slots=True)
class _FileResult:
    outcomes: tuple[Outcome, ...]
    changed: bool


def _accept_file(root: Path, path: str, groups: list[Group], dry_run: bool) -> _FileResult:
    def skipped(reason: str) -> _FileResult:
        return _FileResult(tuple(Outcome(g, "skipped", reason) for g in groups), False)

    file = (root / path).resolve()
    if not file.is_relative_to(root.resolve()) or not file.is_file():
        return skipped(
            f"{path} is not a file under {root.as_posix()}. Run accept from the project root."
        )
    try:
        data = file.read_bytes()
    except OSError as error:
        return skipped(f"{path} could not be read: {error.strerror}.")
    planned = rewrite(path, data, groups)
    if planned.data == data:
        return _FileResult(planned.outcomes, False)
    if dry_run:
        return _FileResult(planned.outcomes, True)
    try:
        _replace(file, planned.data)
    except OSError as error:
        return skipped(f"{path} could not be written: {error.strerror}. It was left unchanged.")
    return _FileResult(planned.outcomes, True)


def _replace(file: Path, data: bytes) -> None:
    temp = file.with_name(f".{file.name}.{secrets.token_hex(4)}.tmp")
    try:
        temp.write_bytes(data)
        shutil.copymode(file, temp)
        os.replace(temp, file)
    finally:
        temp.unlink(missing_ok=True)
    # A same-size edit in the same second passes CPython's and pytest's mtime-and-size pyc check.
    for cached in (file.parent / "__pycache__").glob(f"{file.stem}.*.pyc"):
        cached.unlink(missing_ok=True)


@contextmanager
def lock(lock: Path) -> Generator[None]:
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        shown = lock.as_posix()
        raise AcceptError(
            f"{shown} exists, so another xpt command is writing to .xpt/. "
            f"If none is, delete {shown} and run the command again."
        ) from None
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)
