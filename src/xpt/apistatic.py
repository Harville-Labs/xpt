"""Read a package's public API from its source and stubs, without importing it.

`apisource` parses each module's top-level bindings. This follows `__all__`, star imports, and
re-exports between modules, and turns functions and classes into entries, constructors and
inherited members included.
"""

from __future__ import annotations

import ast
import copy
import pkgutil
from collections.abc import Iterator
from dataclasses import dataclass

from xpt import apimodel
from xpt.apimodel import Entry, Param, Signature
from xpt.apisource import (
    Binding,
    Def,
    Module,
    Ref,
    Var,
    add_def,
    decorators,
    find_spec,
    flat,
    last,
    parse,
)

_PROPERTY = {"property", "cached_property", "abstractproperty"}
_PLAIN_BASES = {"object", "Generic", "Protocol", "ABC"}
_ENUMS = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}
_MAX_BASES = 8


@dataclass(frozen=True, slots=True)
class Found:
    module: Module
    name: str
    binding: Binding


@dataclass(frozen=True, slots=True)
class ModuleRef:
    name: str


@dataclass(frozen=True, slots=True)
class Missing:
    module: str
    reason: str


Lookup = Found | ModuleRef | Missing


class Reader:
    """Parses modules on demand and resolves names through re-exports, caching both."""

    def __init__(self) -> None:
        self._modules: dict[str, Module | None] = {}

    def module(self, name: str) -> Module | None:
        if name not in self._modules:
            self._modules[name] = parse(name)
        return self._modules[name]

    def submodules(self, name: str) -> list[str]:
        module = self.module(name)
        if module is None or not module.search:
            return []
        found = {
            f"{name}.{info.name}"
            for info in pkgutil.iter_modules(module.search)
            if not info.name.startswith("_")
        }
        return sorted(found)

    def exported(self, name: str, seen: frozenset[str] = frozenset()) -> list[str]:
        """The module's public names: its `__all__`, or names without a leading underscore."""
        module = self.module(name)
        if module is None or name in seen:
            return []
        seen |= {name}
        if module.all is not None:
            names = self._all(module, module.all, seen)
            if names is not None:
                return list(dict.fromkeys(names))
        top = name.split(".")[0]
        names = [
            n
            for n, b in module.bindings.items()
            if not n.startswith("_")
            and (
                not isinstance(b, Ref)
                or (b.name is not None and b.module.split(".")[0] == top and not self._is_mod(b))
            )
        ]
        for star in module.stars:
            names += self.exported(star, seen)
        return list(dict.fromkeys(names))

    def all_is_dynamic(self, name: str) -> bool:
        module = self.module(name)
        return (
            module is not None
            and module.all is not None
            and self._all(module, module.all, frozenset({name})) is None
        )

    def _is_mod(self, ref: Ref) -> bool:
        if ref.name is None:
            return True
        target = self.module(ref.module)
        if target is not None and ref.name in target.bindings:
            return False
        return find_spec(f"{ref.module}.{ref.name}") is not None

    def _all(self, module: Module, parts: list[ast.expr], seen: frozenset[str]) -> list[str] | None:
        names: list[str] = []
        for part in parts:
            got = self._names(module, part, seen)
            if got is None:
                return None
            names += got
        return names

    def _names(self, module: Module, node: ast.expr, seen: frozenset[str]) -> list[str] | None:
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            return self._all(module, list(node.elts), seen)
        if isinstance(node, ast.Starred):
            return self._names(module, node.value, seen)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._all(module, [node.left, node.right], seen)
        if isinstance(node, ast.Attribute) and node.attr == "__all__":
            target = self.resolve_expr(module, node.value)
            if isinstance(target, ModuleRef):
                return self.exported(target.name, seen)
        return None

    def lookup(self, module_name: str, name: str, seen: frozenset[str] = frozenset()) -> Lookup:
        key = f"{module_name}:{name}"
        if key in seen:
            return Missing(module_name, f"{name} is imported in a cycle")
        seen |= {key}
        module = self.module(module_name)
        if module is None:
            return Missing(module_name, f"no module {module_name} was found")
        if module.path is None and module.reason:
            return Missing(module_name, module.reason)
        binding = module.bindings.get(name)
        if isinstance(binding, Ref):
            if binding.name is None:
                return ModuleRef(binding.module)
            target = self.lookup(binding.module, binding.name, seen)
            if isinstance(target, Missing) and find_spec(f"{binding.module}.{binding.name}"):
                return ModuleRef(f"{binding.module}.{binding.name}")
            return target
        if binding is not None:
            return Found(module, name, binding)
        for star in module.stars:
            if name in self.exported(star):
                return self.lookup(star, name, seen)
        if module.search and find_spec(f"{module_name}.{name}") is not None:
            return ModuleRef(f"{module_name}.{name}")
        return Missing(module_name, f"{module_name} binds {name} at import time")

    def resolve_expr(self, module: Module, node: ast.expr) -> Lookup:
        if isinstance(node, ast.Subscript):
            return self.resolve_expr(module, node.value)
        if isinstance(node, ast.Name):
            return self.lookup(module.name, node.id)
        if isinstance(node, ast.Attribute):
            owner = self.resolve_expr(module, node.value)
            if isinstance(owner, ModuleRef):
                return self.lookup(owner.name, node.attr)
        return Missing(module.name, f"{ast.unparse(node)} can't be read statically")

    def entry(self, found: Found, name: str) -> Entry:
        binding = found.binding
        if isinstance(binding, Def):
            return _function(name, found.module.name, binding, method=False)
        if isinstance(binding, ast.ClassDef):
            return self._class(found.module, binding, name)
        if isinstance(binding, Var):
            return _variable(name, found.module.name, binding)
        raise AssertionError(binding)

    def member(self, found: Found, name: str) -> Entry | None:
        """One member of a class, including inherited and private ones, for a dotted target."""
        assert isinstance(found.binding, ast.ClassDef)
        for module, node in self._mro(found.module, found.binding):
            members = _members(node, public=False)
            if name in members:
                return _member_entry(name, module.name, members[name])
        return None

    def _mro(self, module: Module, node: ast.ClassDef) -> Iterator[tuple[Module, ast.ClassDef]]:
        """The class, then its bases that resolve statically, depth first, without repeats."""
        seen: set[tuple[str, str]] = set()
        stack = [(module, node)]
        while stack and len(seen) < _MAX_BASES:
            module, node = stack.pop(0)
            if (module.name, node.name) in seen:
                continue
            seen.add((module.name, node.name))
            yield module, node
            for base in node.bases:
                found = self.resolve_expr(module, base)
                if isinstance(found, Found) and isinstance(found.binding, ast.ClassDef):
                    stack.append((found.module, found.binding))

    def _class(self, module: Module, node: ast.ClassDef, name: str) -> Entry:
        top = module.name.split(".")[0]
        members: dict[str, Entry] = {}
        enum = any(last(ast.unparse(b)) in _ENUMS for b in node.bases)
        if enum:
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        var = Var(None, stmt.value)
                        members[target.id] = _variable(target.id, module.name, var)
        for owner, cls in self._mro(module, node):
            if owner.name.split(".")[0] != top:
                continue
            for member, defs in _members(cls, public=True).items():
                if member not in members:
                    members[member] = _member_entry(member, owner.name, defs)
        signatures = (
            (Signature((Param("value", "positional"),)),) if enum else self._init(module, node)
        )
        return Entry(
            name,
            "class",
            module.name,
            signatures=signatures or (),
            known=signatures is not None,
            doc=apimodel.summary(ast.get_docstring(node)),
            members=tuple(members.values()),
        )

    def _init(
        self, module: Module, node: ast.ClassDef, depth: int = 0
    ) -> tuple[Signature, ...] | None:
        """The class's constructor signatures, or None when they can't be read statically."""
        members = _members(node, public=False)
        for special in ("__init__", "__new__"):
            if isinstance(found := members.get(special), Def) and found.shown:
                return tuple(_signature(d).without_first() for d in found.shown)
        bases = [
            last(ast.unparse(b.value if isinstance(b, ast.Subscript) else b)) for b in node.bases
        ]
        dataclass = _dataclass(node)
        if dataclass is not None or "NamedTuple" in bases:
            return (Signature(_fields(node, keyword_only=bool(dataclass))),)
        if "TypedDict" in bases:
            return (
                Signature(
                    tuple(
                        Param(p.name, "keyword_only", p.annotation, p.default)
                        for p in _fields(node)
                    )
                ),
            )
        real = [
            b for b in node.bases if last(ast.unparse(b)) not in _PLAIN_BASES and not _is_plain(b)
        ]
        if not real and not node.keywords:
            return (Signature(()),)
        found = self.resolve_expr(module, real[0]) if real else None
        if (
            depth < _MAX_BASES
            and isinstance(found, Found)
            and isinstance(found.binding, ast.ClassDef)
        ):
            return self._init(found.module, found.binding, depth + 1)
        return None


def _is_plain(base: ast.expr) -> bool:
    return isinstance(base, ast.Subscript) and last(ast.unparse(base.value)) in _PLAIN_BASES


def _dataclass(node: ast.ClassDef) -> list[ast.keyword] | None:
    """The `@dataclass(...)` keywords, empty for a bare `@dataclass`, or None if not one."""
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if last(ast.unparse(target)) == "dataclass":
            return d.keywords if isinstance(d, ast.Call) else []
    return None


def _fields(node: ast.ClassDef, keyword_only: bool = False) -> tuple[Param, ...]:
    """Constructor parameters from annotated fields, as a dataclass or NamedTuple builds them."""
    kw_only = keyword_only and any(
        k.arg == "kw_only" and isinstance(k.value, ast.Constant) and k.value.value is True
        for k in _dataclass(node) or []
    )
    params = []
    for stmt in node.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        marker = last(ast.unparse(stmt.annotation).split("[")[0])
        if marker == "KW_ONLY":
            kw_only = True
            continue
        if marker == "ClassVar":
            continue
        default = None
        if stmt.value is not None:
            default = ast.unparse(stmt.value)
            if isinstance(stmt.value, ast.Call) and last(ast.unparse(stmt.value.func)) == "field":
                keywords = {k.arg: k.value for k in stmt.value.keywords}
                init = keywords.get("init")
                if isinstance(init, ast.Constant) and init.value is False:
                    continue
                if "default" in keywords:
                    default = ast.unparse(keywords["default"])
                elif "default_factory" in keywords:
                    default = "..."
                else:
                    default = None
        kind = "keyword_only" if kw_only else "positional"
        params.append(Param(stmt.target.id, kind, _text(stmt.annotation), default))
    return tuple(params)


def _members(node: ast.ClassDef, public: bool) -> dict[str, Def]:
    """The functions defined in a class body, overloads collected, setters and deleters skipped."""
    found: dict[str, Binding] = {}
    for stmt in flat(node.body):
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if public and stmt.name.startswith("_"):
            continue
        if any(last(d) in ("setter", "deleter", "getter") for d in decorators(stmt)):
            continue
        add_def(found, stmt)
    return {name: d for name, d in found.items() if isinstance(d, Def)}


def _member_entry(name: str, module: str, defs: Def) -> Entry:
    return _function(name, module, defs, method=True)


def _text(node: ast.expr | None) -> str | None:
    """An annotation as text, with forward references unquoted except inside `Literal[...]`."""
    if node is None:
        return None
    return ast.unparse(_Unquote().visit(copy.deepcopy(node)))


class _Unquote(ast.NodeTransformer):
    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        if last(ast.unparse(node.value)) == "Literal":
            return node
        return self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if not isinstance(node.value, str):
            return node
        try:
            return self.visit(ast.parse(node.value, mode="eval").body)
        except (SyntaxError, ValueError):
            return ast.Name(node.value, ast.Load())


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Signature:
    a = node.args
    positional = [*a.posonlyargs, *a.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults)) + list(
        a.defaults
    )
    params = [
        Param(
            arg.arg,
            "positional_only" if i < len(a.posonlyargs) else "positional",
            _text(arg.annotation),
            None if d is None else ast.unparse(d),
        )
        for i, (arg, d) in enumerate(zip(positional, defaults, strict=True))
    ]
    if a.vararg:
        params.append(Param(a.vararg.arg, "var_positional", _text(a.vararg.annotation)))
    for arg, d in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        params.append(
            Param(
                arg.arg,
                "keyword_only",
                _text(arg.annotation),
                None if d is None else ast.unparse(d),
            )
        )
    if a.kwarg:
        params.append(Param(a.kwarg.arg, "var_keyword", _text(a.kwarg.annotation)))
    return Signature(tuple(params), _text(node.returns))


def _function(name: str, module: str, binding: Def, method: bool) -> Entry:
    shown = binding.shown
    first = shown[0]
    marks = {last(d) for d in decorators(first)}
    doc = apimodel.summary(ast.get_docstring(binding.impl or first))
    is_async = isinstance(first, ast.AsyncFunctionDef)
    if method and marks & _PROPERTY:
        return Entry(name, "property", module, value=_text(first.returns) or "", doc=doc)
    signatures = tuple(_signature(d) for d in shown)
    if method and "staticmethod" not in marks:
        signatures = tuple(s.without_first() for s in signatures)
    return Entry(
        name,
        "method" if method else "function",
        module,
        signatures=signatures,
        is_async=is_async,
        doc=doc,
    )


def _variable(name: str, module: str, var: Var) -> Entry:
    text = ""
    if var.annotation is not None:
        text += ": " + apimodel.annotation(_text(var.annotation) or "")
    if var.value is not None:
        text += " = " + apimodel.value(ast.unparse(var.value))
    return Entry(name, "variable", module, value=text)
