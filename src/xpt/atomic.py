from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path


def replace(file: Path, data: bytes) -> None:
    """Write `data` to a temporary file beside `file` and move it into place.

    An existing file keeps its mode. A reader sees the old bytes or the new ones, never a mix.
    """
    temp = file.with_name(f".{file.name}.{secrets.token_hex(4)}.tmp")
    try:
        temp.write_bytes(data)
        if file.exists():
            shutil.copymode(file, temp)
        os.replace(temp, file)
    finally:
        temp.unlink(missing_ok=True)
    # A same-size edit in the same second passes CPython's and pytest's mtime-and-size pyc check.
    for cached in (file.parent / "__pycache__").glob(f"{file.stem}.*.pyc"):
        cached.unlink(missing_ok=True)
