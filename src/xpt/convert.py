from __future__ import annotations

import ast
import codecs
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from xpt import pyfile
from xpt.atomic import replace
from xpt.pyfile import PyFile
from xpt.value import Json

IMPORT = "from xpt import expect"

_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "site-packages"})
_INEXACT = (
    "and == on floats is exact. "
    "Convert it by hand if exact equality is meant, or use expect(..., atol=)."
)


@dataclass(frozen=True, slots=True)
class Skip:
    """An assert, or a whole file when `line` is None, that convert left as it was."""

    path: str
    line: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class FilePlan:
    """A file's new bytes, equal to the old ones when nothing was converted."""

    data: bytes
    converted: tuple[int, ...]
    skipped: tuple[Skip, ...]


@dataclass(frozen=True, slots=True)
class ConvertResult:
    converted: tuple[tuple[str, int], ...]
    skipped: tuple[Skip, ...]
    changed: tuple[str, ...]


class ConvertError(Exception):
    pass


class _Unsafe(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    text: bytes


def convert(root: Path, paths: Sequence[Path], *, dry_run: bool = False) -> ConvertResult:
    """Convert every test file under `paths`, which must lie under `root`.

    A file is rewritten to a temporary file and moved into place. A dry run reports the
    same result and writes nothing.
    """
    base = root.resolve()
    converted: list[tuple[str, int]] = []
    skipped: list[Skip] = []
    changed: list[str] = []
    for file in _targets(base, paths):
        path = file.relative_to(base).as_posix()
        try:
            data = file.read_bytes()
        except OSError as error:
            skipped.append(Skip(path, None, f"{path} could not be read: {error.strerror}."))
            continue
        plan = convert_source(path, data)
        skipped += plan.skipped
        if plan.data == data:
            continue
        if not dry_run:
            try:
                replace(file, plan.data)
            except OSError as error:
                reason = f"{path} could not be written: {error.strerror}. It was left unchanged."
                skipped.append(Skip(path, None, reason))
                continue
        converted += [(path, line) for line in plan.converted]
        changed.append(path)
    return ConvertResult(tuple(converted), tuple(skipped), tuple(changed))


def _targets(base: Path, paths: Sequence[Path]) -> list[Path]:
    found: dict[Path, None] = {}
    for given in paths:
        if not given.exists():
            raise ConvertError(f"{given.as_posix()} does not exist.")
        full = given.resolve()
        if not full.is_relative_to(base):
            raise ConvertError(
                f"{given.as_posix()} is outside the root {base.as_posix()}. "
                "Run xpt convert from the project root or pass --root."
            )
        files = _test_files(full) if full.is_dir() else [full]
        found.update(dict.fromkeys(f for f in files if f.resolve().is_relative_to(base)))
    return sorted(found)


def _test_files(directory: Path) -> Iterator[Path]:
    for file in directory.rglob("*.py"):
        parts = file.relative_to(directory).parts
        if any(p.startswith(".") or p in _SKIPPED_DIRS for p in parts[:-1]):
            continue
        if file.name.startswith("test_") or file.name.endswith("_test.py"):
            yield file


def convert_source(path: str, data: bytes) -> FilePlan:
    """Plan and apply every safe conversion in one file's bytes."""
    bom = codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b""
    source = pyfile.read(path, data[len(bom) :], "convert")
    if isinstance(source, str):
        return FilePlan(data, (), (Skip(path, None, source),))
    bound = _foreign_expect(source.tree)
    edits: list[_Edit] = []
    converted: list[int] = []
    skipped: list[Skip] = []
    for node in _equality_asserts(source.tree):
        try:
            if bound is not None:
                raise _Unsafe(
                    f"line {bound} binds the name expect, so xpt's expect cannot be added."
                )
            edits.append(_edit(source, node))
            converted.append(node.lineno)
        except _Unsafe as error:
            skipped.append(Skip(path, node.lineno, str(error)))
    if not edits:
        return FilePlan(data, (), tuple(skipped))
    if not _imports_expect(source.tree):
        edits.append(_import(source))
    body = source.data
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        body = body[: edit.start] + edit.text + body[edit.end :]
    new = bom + body
    try:
        compile(new, path, "exec", dont_inherit=True)
    except SyntaxError as error:
        reason = (
            f"converting would leave {path} unparsable ({error.msg}, line {error.lineno}). "
            "This is a bug in xpt. Please report it."
        )
        failed = [Skip(path, line, reason) for line in converted]
        return FilePlan(data, (), tuple(sorted([*failed, *skipped], key=_line)))
    return FilePlan(new, tuple(converted), tuple(skipped))


def _line(skip: Skip) -> int:
    return skip.line or 0


def _equality_asserts(tree: ast.Module) -> list[ast.Assert]:
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assert)
        and isinstance(n.test, ast.Compare)
        and any(isinstance(op, ast.Eq) for op in n.test.ops)
    ]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def _edit(source: PyFile, node: ast.Assert) -> _Edit:
    test = node.test
    assert isinstance(test, ast.Compare)
    if node.msg is not None:
        raise _Unsafe("the assert has a message, which expect() has no place for.")
    if len(test.ops) > 1:
        raise _Unsafe("a chained comparison has no single expected value.")
    left, right = test.left, test.comparators[0]
    if _literal(left) and _literal(right):
        raise _Unsafe("both sides are literals, so there is no actual value.")
    if not (_literal(left) or _literal(right)):
        raise _Unsafe("neither side is a literal.")
    actual, expected = (right, left) if _literal(left) else (left, right)
    if problem := _inexact(expected):
        raise _Unsafe(problem)
    start, end = source.span(node)
    kept = [source.span(actual), source.span(expected)]
    if any(start <= c < end and not any(a <= c < b for a, b in kept) for c in source.comments):
        raise _Unsafe("a comment inside the assert would be lost.")
    (a, b), (c, d) = kept
    text = b"expect(" + source.data[a:b] + b", " + source.data[c:d] + b")"
    if not _same(text, actual, expected):
        raise _Unsafe("it could not be rewritten exactly. This is a bug in xpt. Please report it.")
    return _Edit(start, end, text)


def _literal(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _inexact(node: ast.expr) -> str | None:
    for n in ast.walk(node):
        if not isinstance(n, ast.Constant):
            continue
        if isinstance(n.value, float):
            return f"the literal holds a float, {_INEXACT}"
        if isinstance(n.value, complex):
            return f"the literal holds a complex number, {_INEXACT}"
        if isinstance(n.value, bytes):
            return "the literal holds bytes, which xpt cannot record."
        if n.value is Ellipsis:
            return "the literal holds ..., which xpt cannot record."
    return None


def _same(text: bytes, actual: ast.expr, expected: ast.expr) -> bool:
    try:
        call = ast.parse(text.decode("utf-8").replace("\r\n", "\n"), mode="eval").body
    except SyntaxError:
        return False
    return (
        isinstance(call, ast.Call)
        and not call.keywords
        and [ast.dump(a) for a in call.args] == [ast.dump(actual), ast.dump(expected)]
    )


def _foreign_expect(tree: ast.Module) -> int | None:
    """The first line that binds `expect` to anything but xpt's, or None."""
    lines = []
    for n in ast.walk(tree):
        match n:
            case ast.Name(id="expect", ctx=ast.Store() | ast.Del()) | ast.arg(arg="expect"):
                lines.append(n.lineno)
            case ast.FunctionDef(name="expect") | ast.AsyncFunctionDef(name="expect"):
                lines.append(n.lineno)
            case ast.ClassDef(name="expect") | ast.ExceptHandler(name="expect"):
                lines.append(n.lineno)
            case ast.MatchAs(name="expect") | ast.MatchStar(name="expect"):
                lines.append(n.lineno)
            case ast.Import(names=names):
                if any((a.asname or a.name.partition(".")[0]) == "expect" for a in names):
                    lines.append(n.lineno)
            case ast.ImportFrom():
                if any((a.asname or a.name) == "expect" for a in n.names) and not _is_xpt(n):
                    lines.append(n.lineno)
    return min(lines, default=None)


def _is_xpt(node: ast.ImportFrom) -> bool:
    return node.module == "xpt" and not node.level


def _imports_expect(tree: ast.Module) -> bool:
    return any(
        isinstance(n, ast.ImportFrom)
        and _is_xpt(n)
        and any(a.name == "expect" and a.asname in (None, "expect") for a in n.names)
        for n in tree.body
    )


def _import(source: PyFile) -> _Edit:
    """Add the import after the leading docstring and imports, or above the first statement."""
    at, text = pyfile.import_edit(source, [IMPORT])
    return _Edit(at, at, text)


def lines(result: ConvertResult, dry_run: bool, retry: str) -> list[str]:
    out = [
        f"{s.path}{'' if s.line is None else f':{s.line}'}  skipped  {s.reason}"
        for s in result.skipped
    ]
    if not result.converted and not result.skipped:
        return ["Nothing to convert."]
    skipped = f" {len(result.skipped)} skipped." if result.skipped else ""
    if not result.converted:
        return [*out, f"Nothing converted.{skipped}"]
    n = len(result.converted)
    count = f"{n} assert" if n == 1 else f"{n} asserts"
    files = ", ".join(result.changed)
    if dry_run:
        return [*out, f"Dry run, would convert {count} in {files}.{skipped} Next: {retry}"]
    return [*out, f"Converted {count} in {files}.{skipped} Next: run the tests."]


def to_json(result: ConvertResult, dry_run: bool) -> dict[str, Json]:
    return {
        "dry_run": dry_run,
        "converted": [{"path": p, "line": line} for p, line in result.converted],
        "skipped": [{"path": s.path, "line": s.line, "reason": s.reason} for s in result.skipped],
        "changed": list(result.changed),
    }
