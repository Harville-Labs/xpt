"""Frame filtering, exception naming, and chain walking shared by the pytest report and the
compact traceback, so the two print the same frames and the same names."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

OUTSIDE = frozenset({"site-packages", "dist-packages"})
MAX_FRAMES = 4
ELIDED = "..."

T = TypeVar("T")


def project_path(path: Path, root: Path) -> str | None:
    """`path` relative to `root` in POSIX form when it is a project file, else None.

    A project file is under the root and not inside an installed package directory.
    """
    if not path.is_absolute() or not path.is_relative_to(root):
        return None
    relative = path.relative_to(root)
    return None if OUTSIDE.intersection(relative.parts) else relative.as_posix()


def keep(frames: Sequence[T], limit: int = MAX_FRAMES) -> list[T | str]:
    """Frames outermost first, cut to the outermost and the `limit - 1` nearest the error."""
    if len(frames) <= limit:
        return list(frames)
    return [frames[0], ELIDED, *frames[1 - limit :]]


def name(error: BaseException) -> str:
    kind = type(error)
    if kind.__module__ == "builtins" or kind.__module__.startswith("_pytest."):
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def message(error: BaseException) -> str:
    """The exception's text and its notes, one per line."""
    return "\n".join([str(error), *getattr(error, "__notes__", ())])


def one_line(error: BaseException) -> str:
    """`Type: message` with the message's whitespace folded, or the type alone."""
    text = " ".join(str(error).split())
    return f"{name(error)}: {text}" if text else name(error)


def chain(error: BaseException) -> list[tuple[str, BaseException]]:
    """Each exception `error` was chained from, nearest first, with how it was chained:
    "caused by" for `raise X from Y`, "during" for one raised inside an `except` block.
    `raise X from None` ends the chain."""
    links: list[tuple[str, BaseException]] = []
    seen = {id(error)}
    current = error
    while True:
        if current.__cause__ is not None:
            link, current = "caused by", current.__cause__
        elif current.__context__ is not None and not current.__suppress_context__:
            link, current = "during", current.__context__
        else:
            return links
        if id(current) in seen:
            return links
        seen.add(id(current))
        links.append((link, current))


def cause(error: BaseException) -> str | None:
    """The nearest chained exception as one `Type: message` line, as the report prints it."""
    links = chain(error)
    return one_line(links[0][1]) if links else None
