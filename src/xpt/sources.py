"""A digest of a project's Python source, so two runs can tell whether the code changed."""

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
        dirs[:] = [d for d in dirs if _searched(Path(directory, d))]
        for name in files:
            if name.endswith(".py"):
                path = Path(directory, name)
                yield path.relative_to(root).as_posix(), path


def _searched(directory: Path) -> bool:
    name = directory.name
    if name.startswith(".") or name in SKIPPED_DIRS:
        return False
    return not (directory / "pyvenv.cfg").is_file()
