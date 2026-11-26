"""`expect.output`: expect a test's captured stdout and stderr, or a finished subprocess."""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

from xpt import record

_MISSING = object()

ROOT = "<root>"
TMP = "<tmp>"
_END = r"(?![\w-])"
_SEP = r"[\\/]"
_PART = r"[^\\/\s'\"]+"


def output(
    source: object,
    expected: object = _MISSING,
    *,
    normalize: bool = True,
    ignore: Iterable[str] = (),
    key: object = _MISSING,
    _depth: int = 0,
) -> None:
    """Fail unless the output of `source` equals `expected`, line by line.

    `source` is a pytest `capsys` or `capfd` fixture, read with `readouterr()`, or a
    `subprocess.CompletedProcess`, which also records its `returncode`. Each stream is a list
    of lines, so a diff names the line that changed, as in `value["stdout"][3]`.

    By default each line has the project root replaced with `<root>`, the temporary
    directory with `<tmp>`, and trailing whitespace dropped. `normalize=False` records the
    lines as printed. `ignore` and `key` work as they do for `expect()`.
    """
    __tracebackhide__ = True
    from xpt.expect import _MISSING as UNSET
    from xpt.expect import expect

    raw = streams(source)
    rules = Normalizer.for_root(record.recorder.root) if normalize else None
    actual: dict[str, object] = {name: _lines(text, rules) for name, text in raw.items()}
    if isinstance(source, subprocess.CompletedProcess):
        actual["returncode"] = source.returncode
    expect(
        actual,
        UNSET if expected is _MISSING else expected,
        ignore=ignore,
        key=UNSET if key is _MISSING else key,
        _depth=1 + _depth,
        _note="" if rules is None else rules.note(raw.values()),
    )


def streams(source: object) -> dict[str, str | None]:
    """The text of each stream `source` captured, in stdout, stderr order."""
    if isinstance(source, subprocess.CompletedProcess):
        found = {"stdout": source.stdout, "stderr": source.stderr}
        return {name: _text(name, v) for name, v in found.items() if v is not None}
    read = getattr(source, "readouterr", None)
    if callable(read):
        result = read()
        out, err = getattr(result, "out", None), getattr(result, "err", None)
        return {"stdout": _text("stdout", out), "stderr": _text("stderr", err)}
    raise TypeError(
        "expect.output needs the capsys or capfd fixture, or a subprocess.CompletedProcess, "
        f"got {type(source).__name__}. To expect other text, pass its lines to expect(), "
        "as in expect(text.splitlines())."
    )


def _text(name: str, value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8", errors="backslashreplace")
    raise TypeError(f"expect.output read {name} as a {type(value).__name__}, not str or bytes.")


def _lines(text: str | None, rules: Normalizer | None = None) -> list[str]:
    if text is None or text == "":
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if rules is None:
        return lines
    return [rules.apply(line).rstrip() for line in lines]


class Normalizer:
    """The fixed rules: the project root, then the temporary directory, then whitespace."""

    def __init__(self, root: re.Pattern[str] | None, tmp: re.Pattern[str]) -> None:
        self.root = root
        self.tmp = tmp

    @classmethod
    def for_root(cls, root: Path) -> Normalizer:
        temp = Path(tempfile.gettempdir())
        roots = _spellings(root) if len(root.resolve().parts) > 1 else []
        tmp = "|".join(
            rf"(?:{t}){_SEP}pytest-of-{_PART}{_SEP}pytest-\d+(?:{_SEP}{_PART})?{_END}"
            rf"|(?:{t}){_SEP}tmp[\w-]{{8}}{_END}"
            rf"|(?:{t}){_END}"
            for t in map(re.escape, _spellings(temp))
        )
        found = "|".join(map(re.escape, roots))
        return cls(re.compile(rf"(?:{found}){_END}") if found else None, re.compile(tmp))

    def apply(self, line: str) -> str:
        if self.root is not None:
            line = self.root.sub(ROOT, line)
        return self.tmp.sub(TMP, line)

    def note(self, texts: Iterable[str | None]) -> str:
        """Say which rules changed the output, or "" when none did."""
        lines = [line for t in texts for line in _lines(t)]
        rules = []
        if self.root is not None and any(self.root.search(line) for line in lines):
            rules.append(f"the project root -> {ROOT}")
            lines = [self.root.sub(ROOT, line) for line in lines]
        if any(self.tmp.search(line) for line in lines):
            rules.append(f"the temporary directory -> {TMP}")
            lines = [self.tmp.sub(TMP, line) for line in lines]
        if any(line != line.rstrip() for line in lines):
            rules.append("trailing whitespace dropped")
        if not rules:
            return ""
        return f"output normalized: {', '.join(rules)}. normalize=False compares it as printed."


def _spellings(path: Path) -> list[str]:
    """`path` as it may be printed: as given and resolved, native and with forward slashes,
    longest first so a longer spelling wins."""
    resolved = path.resolve()
    found = {str(path), path.as_posix(), str(resolved), resolved.as_posix()}
    return sorted((s.rstrip("\\/") for s in found if s), key=len, reverse=True)
