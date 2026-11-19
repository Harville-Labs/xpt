from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Literal

from xpt.path import Index, Key, Path, field_path
from xpt.tolerance import EXACT, Tolerance
from xpt.value import Bool, Dataclass, Dict, Float, Int, Null, Ref, Seq, Set, Str, Value


@dataclass(frozen=True, slots=True)
class Change:
    path: Path
    op: Literal["replace", "add", "remove"]
    expected: Value | None
    actual: Value | None


@dataclass(frozen=True, slots=True)
class Diff:
    changes: tuple[Change, ...]
    unchanged: int


def diff(expected: Value, actual: Value, tol: Tolerance = EXACT) -> Diff:
    walk = _Walk(tol)
    walk.compare(expected, actual, ())
    return Diff(tuple(walk.changes), walk.unchanged)


def norm(value: Value) -> Hashable:
    """A hashable stand-in that is equal exactly when xpt's comparison says the values are."""
    match value:
        case Str(v) | Int(v) | Bool(v):
            return v
        case Float("nan"):
            return ("float", "nan")
        case Float():
            return value.v
        case Null():
            return None
        case Seq(kind, items):
            return (kind, tuple(norm(i) for i in items))
        case Set(_, items):
            return ("set", frozenset(norm(i) for i in items))
        case Dict(items):
            return ("dict", frozenset((norm(k), norm(v)) for k, v in items))
        case Dataclass(type_id, fields):
            return ("dataclass", type_id, tuple((f, norm(v)) for f, v in fields))
        case Ref(type_id):
            return ("ref", type_id)


def leaves(value: Value) -> int:
    match value:
        case Seq(_, items) | Set(_, items):
            return sum(leaves(i) for i in items)
        case Dict(items):
            return sum(leaves(v) for _, v in items)
        case Dataclass(_, fields):
            return sum(leaves(v) for _, v in fields)
    return 1


@dataclass(slots=True)
class _Walk:
    tol: Tolerance
    changes: list[Change] = field(default_factory=list)
    unchanged: int = 0

    def compare(self, e: Value, a: Value, path: Path) -> None:
        if norm(e) == norm(a) or (self.tol and _close(e, a, self.tol)):
            self.unchanged += leaves(a)
            return
        match e, a:
            case Seq(ek, ei), Seq(ak, ai) if ek == ak:
                for i, (ev, av) in enumerate(zip(ei, ai, strict=False)):
                    self.compare(ev, av, (*path, Index(i)))
                for i in range(len(ai), len(ei)):
                    self.changes.append(Change((*path, Index(i)), "remove", ei[i], None))
                for i in range(len(ei), len(ai)):
                    self.changes.append(Change((*path, Index(i)), "add", None, ai[i]))
            case Set(_, ei), Set(_, ai):
                theirs = {norm(v): v for v in ai}
                for v in ei:
                    if theirs.pop(norm(v), None) is None:
                        self.changes.append(Change(path, "remove", v, None))
                    else:
                        self.unchanged += leaves(v)
                for v in theirs.values():
                    self.changes.append(Change(path, "add", None, v))
            case Dict(ei), Dict(ai):
                theirs_items = {norm(k): (k, v) for k, v in ai}
                for k, v in ei:
                    match theirs_items.pop(norm(k), None):
                        case (_, av):
                            self.compare(v, av, (*path, Key(k)))
                        case None:
                            self.changes.append(Change((*path, Key(k)), "remove", v, None))
                for k, v in theirs_items.values():
                    self.changes.append(Change((*path, Key(k)), "add", None, v))
            case Dataclass(et, ef), Dataclass(at, af) if et == at:
                theirs_fields = dict(af)
                for name, ev in ef:
                    av = theirs_fields.pop(name, None)
                    if av is None:
                        self.changes.append(Change(field_path(path, name), "remove", ev, None))
                    else:
                        self.compare(ev, av, field_path(path, name))
                for name, av in theirs_fields.items():
                    self.changes.append(Change(field_path(path, name), "add", None, av))
            case _:
                self.changes.append(Change(path, "replace", e, a))


def _close(e: Value, a: Value, tol: Tolerance) -> bool:
    match e, a:
        case (Float() | Int(), Float() | Int()) if Float in (type(e), type(a)):
            return tol.close(float(a.v), float(e.v))
    return False
