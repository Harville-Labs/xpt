from __future__ import annotations

import ast
import codecs
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from xpt import layout, pyfile
from xpt.group import Group
from xpt.site import Unlocatable, arg_sha, expected_arg, find_call, scopes_in
from xpt.source import display_width, to_source
from xpt.value import Dataclass, Dict, Ref, Seq, Set, Value

OutcomeStatus = Literal["accepted", "already_accepted", "stale", "skipped", "suspect"]


@dataclass(frozen=True, slots=True)
class Outcome:
    """What accept did with one group. `reason` says why it was not written."""

    group: Group
    status: OutcomeStatus
    reason: str


@dataclass(frozen=True, slots=True)
class Rewrite:
    """A file's new bytes, equal to the old ones when nothing was accepted."""

    data: bytes
    outcomes: tuple[Outcome, ...]


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    text: bytes


class _Unwritable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _File:
    path: str
    data: bytes
    text: str
    starts: tuple[int, ...]
    newline: str
    scopes: dict[str, list[ast.Call]]
    comments: tuple[int, ...]
    imported: frozenset[str]
    modules: dict[str, str]

    def offset(self, line: int, col: int) -> int:
        return self.starts[line - 1] + col

    def before(self, offset: int) -> str:
        start = max(s for s in self.starts if s <= offset)
        return self.data[start:offset].decode("utf-8")

    def after(self, offset: int) -> str:
        end = min((s for s in self.starts if s > offset), default=len(self.data))
        return self.data[offset:end].decode("utf-8").rstrip("\r\n")

    def segment(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.text, node) or ""


def rewrite(path: str, data: bytes, groups: Sequence[Group]) -> Rewrite:
    """Plan every accepted change to one file and apply them, last offset first."""
    bom = codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b""
    file = _read(path, data[len(bom) :])
    if isinstance(file, str):
        return Rewrite(data, tuple(Outcome(g, "skipped", file) for g in groups))
    planned = [_plan(file, g) for g in groups]
    outcomes = [o for o, _ in planned]
    body, lowest = file.data, len(file.data) + 1
    for i, (_, edit) in sorted(enumerate(planned), key=lambda p: _start(p[1][1]), reverse=True):
        if edit is None:
            continue
        if edit.end > lowest:
            reason = f"{path}: this call overlaps another accepted call. Accept them one at a time."
            outcomes[i] = Outcome(outcomes[i].group, "skipped", reason)
            continue
        body, lowest = body[: edit.start] + edit.text + body[edit.end :], edit.start
    new = bom + body
    try:
        compile(new, path, "exec", dont_inherit=True)
    except SyntaxError as error:
        reason = (
            f"accepting would leave {path} unparsable ({error.msg}, line {error.lineno}). "
            "This is a bug in xpt. Please report it."
        )
        return Rewrite(data, tuple(_unless_accepted(o, reason) for o in outcomes))
    return Rewrite(new, tuple(outcomes))


def _start(edit: _Edit | None) -> int:
    return -1 if edit is None else edit.start


def _unless_accepted(outcome: Outcome, reason: str) -> Outcome:
    if outcome.status != "accepted":
        return outcome
    return Outcome(outcome.group, "skipped", reason)


def _read(path: str, data: bytes) -> _File | str:
    source = pyfile.read(path, data, "accept")
    if isinstance(source, str):
        return source
    tree = source.tree
    imported = frozenset(_module_names(tree))
    return _File(
        path,
        source.data,
        source.text,
        source.starts,
        source.newline,
        scopes_in(tree),
        source.comments,
        imported,
        _modules(tree),
    )


def _module_names(tree: ast.Module) -> Iterator[str]:
    for node in tree.body:
        if isinstance(node, ast.Import):
            yield from (a.asname or a.name.partition(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield from (a.asname or a.name for a in node.names)
        elif isinstance(node, ast.ClassDef):
            yield node.name
        elif isinstance(node, ast.Assign):
            yield from (t.id for t in node.targets if isinstance(t, ast.Name))


def _modules(tree: ast.Module) -> dict[str, str]:
    """Map each dotted name the file imports at module level to the expression that names it."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    found[a.name] = a.asname
                else:
                    parts = a.name.split(".")
                    for i in range(1, len(parts) + 1):
                        found.setdefault(".".join(parts[:i]), ".".join(parts[:i]))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                found[f"{node.module}.{a.name}"] = a.asname or a.name
    return found


def _plan(file: _File, group: Group) -> tuple[Outcome, _Edit | None]:
    site = group.site
    if not group.acceptable or isinstance(site, Unlocatable):
        return Outcome(group, "skipped", group.detail), None
    call = find_call(file.scopes.get(site.qualname, []), site.callee, site.ordinal)
    if call is None:
        reason = (
            f"{file.path} no longer has {site.callee}() call {site.ordinal + 1} "
            f"in {site.qualname}. Run the tests again."
        )
        return Outcome(group, "stale", reason), None
    where = f"{file.path}:{call.lineno}"
    try:
        edit = _edit(file, call, group.actuals[0], where)
    except _Unwritable as error:
        return Outcome(group, "skipped", str(error)), None
    if file.data[edit.start : edit.end] == edit.text:
        return Outcome(group, "already_accepted", ""), None
    if arg_sha(file.text, call) != site.arg_sha:
        return Outcome(group, "stale", f"{where} changed since the run. Run the tests again."), None
    return Outcome(group, "accepted", ""), edit


def _edit(file: _File, call: ast.Call, value: Value, where: str) -> _Edit:
    if not call.args or any(isinstance(a, ast.Starred) for a in call.args[:2]):
        raise _Unwritable(
            f"{where} has no plain first argument, "
            "so xpt cannot tell where the expected value goes."
        )
    names = _names(file, value, where)
    start = file.offset(call.lineno, call.col_offset)
    end = file.offset(call.end_lineno or call.lineno, call.end_col_offset or 0)
    if _rebuildable(file, call, start, end):
        text = layout.layout(_call(file, call, value, start, end, names))
        return _Edit(start, end, _encode(file, text))
    expected = expected_arg(call)
    if expected is None:
        actual = call.args[0]
        at = file.offset(actual.end_lineno or actual.lineno, actual.end_col_offset or 0)
        before = file.before(at)
        literal = to_source(
            value, len(_leading(before)), column=display_width(before) + 2, names=names
        )
        return _Edit(at, at, _encode(file, ", " + literal))
    at = file.offset(expected.lineno, expected.col_offset)
    stop = file.offset(expected.end_lineno or expected.lineno, expected.end_col_offset or 0)
    before = file.before(at)
    literal = to_source(value, len(_leading(before)), column=display_width(before), names=names)
    return _Edit(at, stop, _encode(file, literal))


def _rebuildable(file: _File, call: ast.Call, start: int, end: int) -> bool:
    if any(start <= c < end for c in file.comments):
        return False
    if any(isinstance(a, ast.Starred) for a in call.args):
        return False
    return all(k.arg is not None for k in call.keywords)


def _call(
    file: _File, call: ast.Call, value: Value, start: int, end: int, names: dict[str, str]
) -> layout.Call:
    expected = expected_arg(call)
    nodes: list[ast.expr | ast.keyword] = [*call.args, *call.keywords]
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    parts: list[layout.Part] = []
    for node in nodes:
        if node is expected:
            parts.append(layout.Literal("", value))
        elif isinstance(node, ast.keyword) and node.value is expected:
            parts.append(layout.Literal(f"{node.arg}=", value))
        else:
            parts.append(file.segment(node))
        if expected is None and node is call.args[0]:
            parts.append(layout.Literal("", value))
    last = nodes[-1]
    tail = file.data[file.offset(last.end_lineno or 0, last.end_col_offset or 0) : end - 1]
    before = file.before(start)
    return layout.Call(
        head=file.segment(call.func),
        parts=tuple(parts),
        indent=_leading(before),
        before=display_width(before),
        after=display_width(file.after(end)),
        exploded=tail.strip() == b",",
        names=names,
    )


def _leading(line_before: str) -> str:
    return line_before[: len(line_before) - len(line_before.lstrip())]


def _encode(file: _File, text: str) -> bytes:
    return text.replace("\n", file.newline).encode("utf-8")


def _names(file: _File, value: Value, where: str) -> dict[str, str]:
    return {type_id: _name(file, type_id, where) for type_id in sorted(set(_type_ids(value)))}


def _name(file: _File, type_id: str, where: str) -> str:
    module, _, qualname = type_id.partition(":")
    head, dot, rest = qualname.partition(".")
    if module == "builtins":
        return qualname
    if imported := file.modules.get(f"{module}.{head}"):
        return imported + dot + rest
    if alias := file.modules.get(module):
        return f"{alias}.{qualname}"
    if head in file.imported:
        return qualname
    raise _Unwritable(
        f"{where} needs the name {qualname} from {module}, which {file.path} does not import "
        f"at module level. Import {module} or {head} and accept again."
    )


def _type_ids(value: Value) -> Iterator[str]:
    match value:
        case Dataclass(type_id, fields):
            yield type_id
            for _, v in fields:
                yield from _type_ids(v)
        case Ref(type_id):
            yield type_id
        case Seq(_, items) | Set(_, items):
            for v in items:
                yield from _type_ids(v)
        case Dict(items):
            for k, v in items:
                yield from _type_ids(k)
                yield from _type_ids(v)
