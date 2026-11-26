"""A small project with a seeded off-by-one bug, for the `xpt probe` tests.

`window_sums` should return the sum of every run of `size` consecutive values, but its
range stops one window early, so the test of it fails with one sum missing.
"""

from pathlib import Path

FILES = {
    "pyproject.toml": '[project]\nname = "app"\nversion = "0"\n',
    "app/__init__.py": "",
    "app/window.py": """\
def window_sums(values, size):
    # The sum of each run of `size` consecutive values.
    sums = []
    for start in range(len(values) - size):
        chunk = values[start : start + size]
        sums.append(sum(chunk))
    return sums


def describe(values):
    import socket

    sock = socket.socket()
    try:
        return len(values)
    finally:
        sock.close()
""",
    "tests/test_window.py": """\
from app.window import window_sums


def test_window_sums():
    assert window_sums([1, 2, 3, 4], 2) == [3, 5, 7]
""",
    "spawn.py": """\
import subprocess
import sys

from app.window import window_sums

window_sums([1, 2, 3], 1)
CHILD = "from app.window import window_sums; window_sums([5, 6, 7], 2)"
subprocess.run([sys.executable, "-c", CHILD], check=True)
""",
    "threads.py": """\
import threading

from app.window import window_sums

worker = threading.Thread(target=window_sums, args=([1, 2, 3], 1))
worker.start()
worker.join()
""",
    "unsupported.py": """\
from app.window import describe

describe([1, 2])
""",
    "fail.py": """\
from app.window import window_sums

window_sums([1, 2, 3], 1)
raise SystemExit(3)
""",
}

LINE = 6  # sums.append(sum(chunk))


def write(root: Path) -> Path:
    for name, text in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root
