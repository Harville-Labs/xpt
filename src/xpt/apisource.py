"""Find a module's file without importing it, and read its top-level bindings with `ast`.

Nothing here runs the package's code. Modules are found through the import system's finders
(`find_spec` on each finder, which locates files without executing them), and each file is
parsed, never executed. A `.pyi` stub is read instead of the source when one exists.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib.machinery import ModuleSpec
from pathlib import Path

from xpt import apimodel


@dataclass(frozen=True, slots=True)
class Ref:
    """`from module import name`, or `import module` when name is None."""

    module: str
    name: str | None


@dataclass(slots=True)
class Def:
    overloads: list[ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=list)
    impl: ast.FunctionDef | ast.AsyncFunctionDef | None = None

    @property
    def shown(self) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        return self.overloads or ([self.impl] if self.impl else [])


@dataclass(frozen=True, slots=True)
class Var:
    annotation: ast.expr | None
    value: ast.expr | None


Binding = Ref | Def | ast.ClassDef | Var


@dataclass(slots=True)
class Module:
    name: str
    package: bool
    path: Path | None = None
    reason: str = ""
    doc: str = ""
    bindings: dict[str, Binding] = field(default_factory=dict)
    stars: list[str] = field(default_factory=list)
    all: list[ast.expr] | None = None
    search: list[str] = field(default_factory=list)


def find_spec(name: str) -> ModuleSpec | None:
    """The spec for a dotted module name, found without importing any of its parents."""
    parts = name.split(".")
    try:
        spec = importlib.util.find_spec(parts[0])
    except (ImportError, ValueError):
        return None
    for i in range(1, len(parts)):
        if spec is None or spec.submodule_search_locations is None:
            return None
        full = ".".join(parts[: i + 1])
        loaded = sys.modules.get(full)
        if loaded is not None and getattr(loaded, "__spec__", None) is not None:
            spec = loaded.__spec__
            continue
        spec = _find_child(full, list(spec.submodule_search_locations))
    return spec


def _find_child(full: str, search: list[str]) -> ModuleSpec | None:
    for finder in sys.meta_path:
        find = getattr(finder, "find_spec", None)
        if find is None:
            continue
        try:
            spec = find(full, search)
        except Exception:
            continue
        if spec is not None:
            return spec
    return None


def _stub_package(name: str) -> Path | None:
    top, *rest = name.split(".")
    for entry in sys.path:
        base = Path(entry or ".") / f"{top}-stubs"
        if base.is_dir():
            here = base.joinpath(*rest)
            for candidate in (here / "__init__.pyi", here.with_suffix(".pyi")):
                if candidate.is_file():
                    return candidate
    return None


def _file(spec: ModuleSpec) -> tuple[Path | None, str]:
    if (stub := _stub_package(spec.name)) is not None:
        return stub, ""
    origin = spec.origin
    if origin == "frozen":
        origin = getattr(spec.loader_state, "filename", None)
    if origin in (None, "built-in", "frozen"):
        if spec.submodule_search_locations is not None:
            return None, ""
        return None, f"{spec.name} is built into the interpreter"
    path = Path(origin)
    if path.suffix == ".py":
        stub = path.with_suffix(".pyi")
        return (stub if stub.is_file() else path), ""
    if path.suffix == ".pyi":
        return path, ""
    if any(path.name.endswith(s) for s in importlib.machinery.EXTENSION_SUFFIXES):
        stub = path.with_name(path.name.split(".")[0] + ".pyi")
        if stub.is_file():
            return stub, ""
        return None, f"{spec.name} is an extension module with no .pyi stub"
    return None, f"{spec.name} has no source file"


def decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    names = []
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        names.append(ast.unparse(target))
    return names


def last(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(last(d) == "overload" for d in decorators(node))


def _known(test: ast.expr) -> bool | None:
    """The value of a `sys.version_info` or `sys.platform` comparison, as stubs branch on them."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    left, op, right = ast.unparse(test.left), test.ops[0], test.comparators[0]
    try:
        literal = ast.literal_eval(right)
    except (ValueError, TypeError, SyntaxError):
        return None
    actual: object
    if left == "sys.version_info" and isinstance(literal, tuple):
        actual = sys.version_info[: len(literal)]
    elif left == "sys.platform" and isinstance(literal, str):
        actual = sys.platform
    else:
        return None
    compare = {
        ast.GtE: lambda a, b: a >= b,
        ast.Gt: lambda a, b: a > b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
    }.get(type(op))
    return None if compare is None else bool(compare(actual, literal))


def flat(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    for stmt in body:
        if isinstance(stmt, ast.If):
            known = _known(stmt.test)
            if known is not False:
                yield from flat(stmt.body)
            if known is not True:
                yield from flat(stmt.orelse)
        elif isinstance(stmt, ast.Try | ast.TryStar):
            yield from flat(stmt.body)
            for handler in stmt.handlers:
                yield from flat(handler.body)
            yield from flat(stmt.orelse)
            yield from flat(stmt.finalbody)
        else:
            yield stmt


def add_def(into: dict[str, Binding], node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    existing = into.get(node.name)
    if not isinstance(existing, Def):
        if existing is not None and not isinstance(existing, Ref | Var):
            return
        existing = into[node.name] = Def()
    if _is_overload(node):
        existing.overloads.append(node)
    elif existing.impl is None:
        existing.impl = node


def parse(name: str) -> Module | None:
    """The module's top-level bindings, read from its stub or source, or None if not found."""
    spec = find_spec(name)
    if spec is None:
        return None
    search = list(spec.submodule_search_locations or [])
    path, reason = _file(spec)
    module = Module(name, bool(search), path, reason, search=search)
    if path is None:
        return module
    try:
        tree = ast.parse(path.read_bytes(), str(path))
    except (OSError, SyntaxError, ValueError) as error:
        module.path, module.reason = None, f"{path.name} could not be parsed: {error}"
        return module
    module.doc = apimodel.summary(ast.get_docstring(tree))
    _Collector(module).walk(tree.body)
    return module


class _Collector:
    """Fills a Module's bindings from its top-level statements, as an import would bind them."""

    def __init__(self, module: Module) -> None:
        self.module = module

    def bind(self, name: str, binding: Binding) -> None:
        """Bind a name once. Only a def or class replaces an import or an assignment."""
        existing = self.module.bindings.get(name)
        definition = (Def, ast.ClassDef)
        if existing is None or (
            isinstance(binding, definition) and not isinstance(existing, definition)
        ):
            self.module.bindings[name] = binding

    def absolute(self, node: ast.ImportFrom) -> str:
        if node.level == 0:
            return node.module or ""
        parts = self.module.name.split(".")
        base = parts if self.module.package else parts[:-1]
        base = base[: len(base) - (node.level - 1)]
        return ".".join([*base, *([node.module] if node.module else [])])

    def walk(self, body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                add_def(self.module.bindings, stmt)
            elif isinstance(stmt, ast.ClassDef):
                self.bind(stmt.name, stmt)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    self.assign(target, None, stmt.value)
            elif isinstance(stmt, ast.AnnAssign):
                self.assign(stmt.target, stmt.annotation, stmt.value)
            elif isinstance(stmt, ast.AugAssign) and _is_all(stmt.target):
                if self.module.all is not None:
                    self.module.all.append(stmt.value)
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                self.extend_all(stmt.value)
            elif isinstance(stmt, ast.ImportFrom):
                base = self.absolute(stmt)
                for alias in stmt.names:
                    if alias.name == "*":
                        self.module.stars.append(base)
                    else:
                        self.bind(alias.asname or alias.name, Ref(base, alias.name))
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.asname:
                        self.bind(alias.asname, Ref(alias.name, None))
                    else:
                        top = alias.name.split(".")[0]
                        self.bind(top, Ref(top, None))
            elif isinstance(stmt, ast.If | ast.Try | ast.TryStar):
                self.walk(list(flat([stmt])))

    def assign(self, target: ast.expr, annotation: ast.expr | None, value: ast.expr | None) -> None:
        if not isinstance(target, ast.Name):
            return
        if target.id == "__all__":
            if value is not None:
                self.module.all = [value]
        else:
            self.bind(target.id, Var(annotation, value))

    def extend_all(self, call: ast.Call) -> None:
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and _is_all(func.value)
            and func.attr in ("extend", "append")
            and self.module.all is not None
            and len(call.args) == 1
        ):
            arg = call.args[0]
            self.module.all.append(ast.List([arg], ast.Load()) if func.attr == "append" else arg)


def _is_all(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"
