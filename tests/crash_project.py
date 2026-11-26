"""A small project whose scripts crash in a library, in a chain, in recursion, and in a
thread, shared by the compact traceback tests and the pytest report tests."""

from pathlib import Path

FILES = {
    "pyproject.toml": '[project]\nname = "app"\nversion = "0"\n',
    "lib/site-packages/fakelib/__init__.py": "from fakelib.core import parse\n",
    "lib/site-packages/fakelib/core.py": """\
def parse(text):
    return _convert(text.strip())


def _convert(text):
    return int(text)
""",
    "app/__init__.py": "",
    "app/load.py": """\
import fakelib


def read_row(row):
    count = row["count"]
    return fakelib.parse(count)


def load(rows):
    counts = []
    for row in rows:
        counts.append(read_row(row))
    return counts
""",
    "app/chain.py": """\
def lookup(row):
    try:
        return row["count"]
    except KeyError as error:
        raise ValueError("no count") from error


def load(row):
    try:
        return lookup(row)
    except ValueError:
        raise RuntimeError("load failed")
""",
    "app/deep.py": """\
def depth(n):
    return depth(n + 1)
""",
    "library.py": """\
from app.load import load

rows = [{"count": "3"}, {"count": "x"}]
load(rows)
""",
    "chained.py": """\
from app.chain import load

load({"name": "ada"})
""",
    "recursion.py": """\
from app.deep import depth


def main():
    depth(0)


main()
""",
    "thread.py": """\
import threading

from app.load import read_row


def work():
    read_row({"count": "x"})


worker = threading.Thread(target=work, name="worker")
worker.start()
worker.join()
print("done")
""",
}

TESTS = """\
from app.chain import load as load_chain
from app.deep import depth
from app.load import load


def test_library():
    load([{"count": "x"}])


def test_chained():
    load_chain({"name": "ada"})


def test_recursion():
    depth(0)
"""


def write(root: Path) -> Path:
    for name, text in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root
