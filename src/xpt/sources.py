"""Which files are the project's own Python source, and a digest of them, so two runs can tell
whether the code changed."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "site-packages"})


def digest(root: Path) -> str:
    """The sha256 of every `.py` file's path and contents under `root`.

    Hidden directories, `__pycache__`, `node_modules`, `site-packages`, and virtual
    environments are skipped. A file that cannot be read counts by its path alone.
    """
    total = hashlib.sha256()
    for relative, path in sorted(_files(root)):
        try:
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            content = ""
        total.update(f"{relative}\0{content}\n".encode())
    return total.hexdigest()


def _files(root: Path) -> Iterator[tuple[str, Path]]:
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if searched(Path(directory, d))]
        for name in files:
            if name.endswith(".py"):
                path = Path(directory, name)
                yield path.relative_to(root).as_posix(), path


def project_file(root: Path, filename: str) -> str | None:
    """`filename` as a path relative to `root`, when it is a `.py` file of the project.

    It is None outside `root` and under a directory `digest` skips, such as a virtual
    environment or `site-packages`, so installed packages never count as the project.
    """
    path = Path(filename)
    if path.suffix != ".py" or not path.is_absolute():
        return None
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        try:
            parts = Path(os.path.realpath(path)).relative_to(os.path.realpath(root)).parts
        except ValueError:
            return None
    for depth in range(1, len(parts)):
        if not searched(root.joinpath(*parts[:depth])):
            return None
    return "/".join(parts)


def searched(directory: Path) -> bool:
    """Whether a directory holds project source: not hidden, not a cache, not a venv."""
    name = directory.name
    if name.startswith(".") or name in SKIPPED_DIRS:
        return False
    return not (directory / "pyvenv.cfg").is_file()
