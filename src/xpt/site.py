from __future__ import annotations

import ast
import hashlib
import itertools
import json
import linecache
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import FrameType

SiteKey = tuple[str, str, str, int]


@dataclass(frozen=True, slots=True)
class CallSite:
    path: str
    qualname: str
    callee: str
    ordinal: int
    arg_sha: str
    line: int

    @property
    def key(self) -> SiteKey:
        return (self.path, self.qualname, self.callee, self.ordinal)


@dataclass(frozen=True, slots=True)
class Unlocatable:
    reason: str
    path: str
    line: int


def call_site(depth: int, root: Path) -> CallSite | Unlocatable:
    """Identify a call on the stack.

    Depth 0 is the call to `call_site` itself, and depth 1 is the call to the function that
    called `call_site`, which is what `expect()` identifies.
    """
    return locate(sys._getframe(depth + 1), root)


def locate(frame: FrameType, root: Path) -> CallSite | Unlocatable:
    """Identify the call that `frame` is running, by function, ordinal, and argument text."""
    code = frame.f_code
    filename = code.co_filename
    path = _relative_path(filename, root)
    lineno, end_lineno, col, end_col = next(
        itertools.islice(code.co_positions(), frame.f_lasti // 2, None)
    )
    line = lineno or frame.f_lineno or 0

    def unlocatable(reason: str) -> Unlocatable:
        return Unlocatable(reason, path, line)

    linecache.checkcache(filename)
    source = "".join(linecache.getlines(filename, frame.f_globals))
    if not source or not Path(filename).is_file():
        return unlocatable(
            f"{path} has no source file; the comparison still runs, but xpt cannot rewrite it."
        )
    found_scopes = scopes(filename, source)
    if isinstance(found_scopes, SyntaxError):
        return unlocatable(
            f"{path} no longer parses ({found_scopes.msg}, line {found_scopes.lineno}). "
            "Fix the file and run the tests again."
        )
    qualname = _enclosing_qualname(code.co_qualname)
    calls = found_scopes.get(qualname, [])
    if col is None:
        found = _outermost_on_line(calls, line)
        if len(found) > 1:
            return unlocatable(
                f"{path}:{line} has {len(found)} calls and Python was run with "
                f"-X no_debug_ranges, so xpt cannot tell which one ran on line {line}. "
                "Run without -X no_debug_ranges, or put one expect() per line."
            )
    else:
        found = [c for c in calls if _span(c) == (lineno, end_lineno, col, end_col)]
    if not found:
        return unlocatable(
            f"{path}:{line} has no call in {qualname} where the running code expects one. "
            "The file probably changed after it was imported. Run the tests again."
        )
    call = found[0]
    return CallSite(path, qualname, callee(call), ordinal(calls, call), arg_sha(source, call), line)


def callee(call: ast.Call) -> str:
    return ast.unparse(call.func)


def ordinal(calls: list[ast.Call], call: ast.Call) -> int:
    """Count the calls to the same callee before `call` in its scope."""
    name = callee(call)
    before = itertools.takewhile(lambda c: c is not call, calls)
    return sum(1 for c in before if callee(c) == name)


def find_call(calls: list[ast.Call], name: str, index: int) -> ast.Call | None:
    """Find the call that `ordinal` numbers `index` among the calls to `name`."""
    same = [c for c in calls if callee(c) == name]
    return same[index] if index < len(same) else None


def expected_arg(call: ast.Call) -> ast.expr | None:
    if len(call.args) > 1:
        return call.args[1]
    return next((k.value for k in call.keywords if k.arg == "expected"), None)


def arg_sha(source: str, call: ast.Call) -> str:
    expected = expected_arg(call)
    if expected is None:
        return ""
    segment = ast.get_source_segment(source, expected) or ""
    return hashlib.sha256(segment.encode("utf-8")).hexdigest()


def site_id(site: CallSite) -> str:
    return hashlib.sha256(json.dumps(list(site.key)).encode("utf-8")).hexdigest()


def short_ids(sites: Iterable[CallSite]) -> dict[SiteKey, str]:
    """Give each distinct site the shortest id prefix, at least 4 characters, unique in the set."""
    full = sorted({site_id(s): s.key for s in sites}.items())
    shared = [_common_prefix(a, b) for (a, _), (b, _) in itertools.pairwise(full)]
    ids: dict[SiteKey, str] = {}
    for i, (hex_id, key) in enumerate(full):
        before = shared[i - 1] if i > 0 else 0
        after = shared[i] if i < len(shared) else 0
        ids[key] = hex_id[: max(4, before + 1, after + 1)]
    return ids


def _common_prefix(a: str, b: str) -> int:
    return next((i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y), len(a))


def _relative_path(filename: str, root: Path) -> str:
    if filename.startswith("<"):
        return filename
    path = Path(filename).resolve()
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


_COMPREHENSION = re.compile(r"(?:\.<locals>)?\.?<(?:listcomp|setcomp|dictcomp|genexpr)>$")


def _enclosing_qualname(qualname: str) -> str:
    while (stripped := _COMPREHENSION.sub("", qualname)) != qualname:
        qualname = stripped
    return qualname or "<module>"


def _span(node: ast.expr) -> tuple[int, int | None, int, int | None]:
    return (node.lineno, node.end_lineno, node.col_offset, node.end_col_offset)


def _outermost_on_line(calls: list[ast.Call], line: int) -> list[ast.Call]:
    starting = [c for c in calls if c.lineno == line]
    inner = {id(n) for c in starting for n in ast.walk(c) if n is not c}
    return [c for c in starting if id(c) not in inner]


@lru_cache(maxsize=64)
def scopes(filename: str, source: str) -> dict[str, list[ast.Call]] | SyntaxError:
    try:
        tree = ast.parse(source, filename)
    except SyntaxError as error:
        return error
    scopes: dict[str, list[ast.Call]] = {}
    _collect(tree.body, "<module>", "", scopes)
    for calls in scopes.values():
        calls.sort(key=lambda c: (c.lineno, c.col_offset))
    return scopes


def _collect(
    nodes: Iterable[ast.AST], qualname: str, prefix: str, scopes: dict[str, list[ast.Call]]
) -> None:
    calls = scopes.setdefault(qualname, [])
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            calls.append(node)
        inner = _inner_scope(node)
        if inner is None:
            stack.extend(ast.iter_child_nodes(node))
            continue
        name, body, is_function = inner
        child = prefix + name
        _collect(body, child, child + (".<locals>." if is_function else "."), scopes)
        inside = {id(n) for n in body}
        stack.extend(n for n in ast.iter_child_nodes(node) if id(n) not in inside)


def _inner_scope(node: ast.AST) -> tuple[str, list[ast.AST], bool] | None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name, list(node.body), True
    if isinstance(node, ast.ClassDef):
        return node.name, list(node.body), False
    if isinstance(node, ast.Lambda):
        return "<lambda>", [node.body], True
    return None
