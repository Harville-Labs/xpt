"""`xpt api`: compact signatures for the installed version of a package.

Names are read statically by default (see `apistatic`), so listing a package runs none of its
code. `--import` falls back to `inspect` on the imported module for what `ast` can't read.
"""

from __future__ import annotations

import ast
import importlib.metadata
import inspect
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace

from xpt import apiimport, apimodel
from xpt.apimodel import Entry
from xpt.apistatic import Found, Missing, ModuleRef, Reader, find_spec

LIMIT = 80


class ApiError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Section:
    module: str
    doc: str
    entries: tuple[Entry, ...]


@dataclass(frozen=True, slots=True)
class Listing:
    target: str
    package: str
    version: str | None
    stdlib: bool
    sections: tuple[Section, ...]
    more: int
    notes: tuple[str, ...]
    use_import: bool

    @property
    def unresolved(self) -> int:
        return sum(_count(s.entries, lambda e: e.kind == "unresolved") for s in self.sections)


def _count(entries: tuple[Entry, ...], keep: Callable[[Entry], bool] = lambda e: True) -> int:
    return sum(keep(e) + _count(e.members, keep) for e in entries)


def version(top: str) -> tuple[str | None, bool]:
    """The installed distribution's version, and whether the module is in the standard library."""
    if top in sys.stdlib_module_names:
        return None, True
    names = importlib.metadata.packages_distributions().get(top, [])
    for dist in [*names, top, top.replace("_", "-")]:
        try:
            return importlib.metadata.version(dist), False
        except importlib.metadata.PackageNotFoundError:
            continue
    return None, False


class _View:
    def __init__(self, use_import: bool) -> None:
        self.reader = Reader()
        self.use_import = use_import
        self.notes: list[str] = []

    def imported(self, module: str, path: list[str], fallback: Entry) -> Entry:
        if not self.use_import:
            return fallback
        obj = apiimport.load(module)
        if isinstance(obj, str):
            return apimodel.unresolved(fallback.name, module, obj)
        owner: object = None
        for part in path:
            owner, obj = obj, getattr(obj, part, _MISSING)
            if obj is _MISSING:
                return apimodel.unresolved(fallback.name, module, f"{module} has no {part}")
        if inspect.isclass(owner):
            raw = inspect.getattr_static(owner, path[-1], obj)
            found = apiimport.member(owner, path[-1], raw, public=False)
            if found is not None:
                return replace(found, name=fallback.name)
        return apiimport.entry(fallback.name, obj, module)

    def name(self, module: str, name: str) -> Entry:
        found = self.reader.lookup(module, name)
        if isinstance(found, ModuleRef):
            target = self.reader.module(found.name)
            return Entry(name, "module", module, value=found.name, doc=target.doc if target else "")
        if isinstance(found, Missing):
            return self.imported(module, [name], apimodel.unresolved(name, module, found.reason))
        entry = self.reader.entry(found, name)
        if entry.known:
            return entry
        imported = self.imported(module, [name], entry)
        if imported is entry or imported.kind == "unresolved":
            return entry
        return replace(
            entry, signatures=imported.signatures, known=imported.known, resolved="import"
        )

    def module(self, name: str) -> tuple[Entry, ...]:
        module = self.reader.module(name)
        assert module is not None
        if module.path is None and module.reason:
            if not self.use_import:
                self.notes.append(
                    f"{module.reason}, so its names can't be read statically. Rerun with --import "
                    "to read them from the imported module, which runs its code."
                )
                return ()
            loaded = apiimport.load(name)
            if isinstance(loaded, str):
                self.notes.append(loaded)
                return ()
            return tuple(
                apiimport.entry(n, getattr(loaded, n), name)
                for n in apiimport.exported(loaded)
                if hasattr(loaded, n)
            )
        names = self.reader.exported(name)
        if self.reader.all_is_dynamic(name):
            loaded = apiimport.load(name) if self.use_import else None
            if loaded is not None and not isinstance(loaded, str):
                names = apiimport.exported(loaded)
            else:
                self.notes.append(
                    f"{name}.__all__ is computed at import time, so this lists the names without "
                    "a leading underscore. Rerun with --import to read __all__."
                )
        return tuple(self.name(name, n) for n in names)

    def target(self, module: str, rest: list[str]) -> Entry | str:
        """The entry a dotted path names below a module, or the name of a module it names."""
        found = self.reader.lookup(module, rest[0])
        if isinstance(found, ModuleRef):
            return self.target(found.name, rest[1:]) if rest[1:] else found.name
        if len(rest) == 1:
            return self.name(module, rest[0])
        dotted = ".".join(rest)
        if len(rest) == 2 and isinstance(found, Found) and isinstance(found.binding, ast.ClassDef):
            member = self.reader.member(found, rest[1])
            if member is not None:
                return replace(member, name=dotted)
        fallback = apimodel.unresolved(dotted, module, f"{module} has no {dotted} in its source")
        entry = self.imported(module, rest, fallback)
        if entry.kind == "unresolved" and not self.use_import and isinstance(found, Found):
            raise ApiError(
                f"{module}.{rest[0]} has no member {'.'.join(rest[1:])} in its source. "
                "Run xpt api on the class to list its members."
            )
        return entry


_MISSING = object()


def listing(
    target: str,
    depth: int = 0,
    grep: str | None = None,
    limit: int = LIMIT,
    use_import: bool = False,
) -> Listing:
    parts = target.split(".")
    if not all(p.isidentifier() for p in parts):
        raise ApiError(f"{target!r} is not a dotted Python name, as in json or json.dumps.")
    view = _View(use_import)
    for i in range(len(parts), 0, -1):
        if find_spec(".".join(parts[:i])) is not None:
            break
    else:
        raise ApiError(
            f"no module {parts[0]} is installed for {sys.executable}. Run xpt with the Python "
            f"of the environment that has it, as in uv run xpt api {target}."
        )
    try:
        sections = _sections(view, ".".join(parts[:i]), parts[i:], depth)
    except RecursionError:
        raise ApiError(
            f"{target} re-exports names in a cycle too deep to follow statically."
        ) from None
    top = parts[0]
    pkg_version, stdlib = version(top)
    sections = _grep(sections, grep) if grep else sections
    shown, more = _limit(sections, limit)
    return Listing(target, top, pkg_version, stdlib, shown, more, tuple(view.notes), use_import)


def _sections(view: _View, module: str, rest: list[str], depth: int) -> list[Section]:
    if rest:
        found = view.target(module, rest)
        if isinstance(found, Entry):
            return [Section(module, "", (found,))]
        module = found
    return _modules(view, module, depth)


def _modules(view: _View, module: str, depth: int) -> list[Section]:
    names = [module]
    frontier = [module]
    for _ in range(depth):
        frontier = [sub for m in frontier for sub in view.reader.submodules(m)]
        names += frontier
    seen: set[tuple[str, str]] = set()
    sections = []
    for name in sorted(names, key=lambda n: (n != module, n)):
        entries = []
        for entry in view.module(name):
            key = (entry.module, entry.name) if entry.kind != "module" else (entry.value, "")
            if entry.kind == "unresolved" or key not in seen:
                seen.add(key)
                entries.append(entry)
        info = view.reader.module(name)
        sections.append(Section(name, info.doc if info else "", tuple(entries)))
    return sections


def _grep(sections: list[Section], word: str) -> list[Section]:
    word = word.lower()
    out = []
    for section in sections:
        kept = []
        for entry in section.entries:
            if word in entry.name.lower():
                kept.append(entry)
            elif members := tuple(m for m in entry.members if word in m.name.lower()):
                kept.append(replace(entry, members=members))
        out.append(replace(section, entries=tuple(kept)))
    return out


def _limit(sections: list[Section], limit: int) -> tuple[tuple[Section, ...], int]:
    """Keep the first `limit` names (classes and their members each count), and count the rest."""
    total = sum(_count(s.entries) for s in sections)
    if limit <= 0 or total <= limit:
        return tuple(sections), 0
    budget = limit
    out = []
    for section in sections:
        kept = []
        for entry in section.entries:
            if budget <= 0:
                break
            budget -= 1
            members = entry.members[: max(budget, 0)]
            budget -= len(members)
            kept.append(replace(entry, members=members))
        out.append(replace(section, entries=tuple(kept)))
    return tuple(out), total - limit


def lines(result: Listing) -> list[str]:
    if result.stdlib:
        head = f"{result.package} stdlib (Python {platform.python_version()})"
    elif result.version is None:
        head = f"{result.package} (no installed distribution found)"
    else:
        head = f"{result.package} {result.version}"
    out = [head]
    for i, section in enumerate(result.sections):
        if i > 0 and section.entries:
            out.append(f"module {section.module}" + (f"  {section.doc}" if section.doc else ""))
        for entry in section.entries:
            out += apimodel.lines(entry)
    if result.more:
        out.append(f"... {result.more} more. Use --grep WORD to filter, or --limit 0 to list all.")
    if result.unresolved and not result.use_import:
        out.append(
            f"{result.unresolved} unresolved: not readable from source or stubs. Rerun with "
            "--import to read them from the imported module, which runs its code."
        )
    out += result.notes
    return out


def to_json(result: Listing) -> dict[str, object]:
    return {
        "target": result.target,
        "package": result.package,
        "version": result.version,
        "stdlib": result.stdlib,
        "python": platform.python_version(),
        "modules": [
            {
                "name": s.module,
                "doc": s.doc,
                "entries": [apimodel.to_json(e) for e in s.entries],
            }
            for s in result.sections
        ],
        "more": result.more,
        "unresolved": result.unresolved,
        "notes": list(result.notes),
    }
