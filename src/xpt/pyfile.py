from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass

_NEWLINE = re.compile(rb"\r\n|\r|\n")
_UTF_8 = ("utf-8", "utf-8-sig")


@dataclass(frozen=True)
class PyFile:
    """A UTF-8 Python file with the byte offsets an edit to it needs."""

    path: str
    data: bytes
    text: str
    tree: ast.Module
    starts: tuple[int, ...]
    newline: str
    comments: tuple[int, ...]

    def offset(self, line: int, col: int) -> int:
        return self.starts[line - 1] + col

    def span(self, node: ast.stmt | ast.expr) -> tuple[int, int]:
        end = self.offset(node.end_lineno or node.lineno, node.end_col_offset or 0)
        return self.offset(node.lineno, node.col_offset), end


def read(path: str, data: bytes, command: str) -> PyFile | str:
    """Parse `data`, or say in one sentence why `xpt <command>` can't write this file."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except SyntaxError as error:
        return f"{path} has an unreadable encoding declaration ({error.msg})."
    if encoding not in _UTF_8:
        return f"{path} is encoded as {encoding}. xpt {command} writes only UTF-8 files."
    try:
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as error:
        return f"{path} is not valid UTF-8 ({error.reason} at byte {error.start})."
    try:
        tree = ast.parse(text, path)
    except SyntaxError as error:
        return (
            f"{path} does not parse ({error.msg}, line {error.lineno}). Fix it and {command} again."
        )
    starts = (0, *(m.end() for m in _NEWLINE.finditer(data)))
    first = _NEWLINE.search(data)
    comments = tuple(
        starts[t.start[0] - 1] + len(t.line[: t.start[1]].encode("utf-8"))
        for t in tokenize.generate_tokens(io.StringIO(text).readline)
        if t.type == tokenize.COMMENT
    )
    newline = first.group().decode("ascii") if first else "\n"
    return PyFile(path, data, text, tree, starts, newline, comments)
