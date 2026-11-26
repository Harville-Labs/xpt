"""`xpt env`: the interpreter, modules that shadow the standard library or an installed
package, and installed versions that drift from the lockfile.

Everything here reads files and package metadata. Nothing imports a user module or writes.
"""

from __future__ import annotations

import importlib.machinery
import importlib.metadata
import json
import os
import re
import sys
import sysconfig
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from xpt import lockfile
from xpt.lockfile import normalize
from xpt.traceback import project_root
from xpt.value import Json

_NOT_MODULES = frozenset({"__main__", "__init__"})

Kind = Literal["file", "namespace", "built-in", "frozen", "missing"]


@dataclass(frozen=True, slots=True)
class Installed:
    name: str
    version: str
    dist: importlib.metadata.Distribution


@dataclass(frozen=True, slots=True)
class Interpreter:
    version: str
    executable: str
    venv: str | None
    problems: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Shadow:
    path: str
    module: str
    stdlib: bool
    distributions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Drift:
    name: str
    lockfile: str
    locked: tuple[str, ...]
    installed: str | None


@dataclass(frozen=True, slots=True)
class Report:
    interpreter: Interpreter
    lockfiles: tuple[str, ...]
    shadowing: tuple[Shadow, ...]
    drift: tuple[Drift, ...]
    unreadable: tuple[str, ...]

    @property
    def has_problems(self) -> bool:
        return bool(self.interpreter.problems or self.shadowing or self.drift or self.unreadable)


@dataclass(frozen=True, slots=True)
class Found:
    name: str
    kind: Kind
    origin: str | None
    locations: tuple[str, ...]
    stdlib: bool
    distribution: str | None
    version: str | None
    shadows: str | None


def installed() -> dict[str, Installed]:
    """Each installed distribution by normalized name, the first on `sys.path` winning as
    import would."""
    found: dict[str, Installed] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if name and normalize(name) not in found:
            found[normalize(name)] = Installed(name, dist.version, dist)
    return found


def report(root: Path, environ: Mapping[str, str]) -> Report:
    have = installed()
    lockfiles, drift, unreadable = _drift(root, have)
    return Report(
        _interpreter(root, environ),
        lockfiles,
        tuple(shadowing(root, have)),
        tuple(drift),
        tuple(unreadable),
    )


def _show(path: Path, root: Path) -> str:
    """`path` relative to `root` in POSIX form when it is inside, else absolute."""
    path = Path(os.path.abspath(path))
    for base in (root, root.resolve()):
        if path.is_relative_to(base):
            return path.relative_to(base).as_posix() or "."
    return str(path)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _interpreter(root: Path, environ: Mapping[str, str]) -> Interpreter:
    info = sys.version_info
    version = f"{info.major}.{info.minor}.{info.micro}"
    prefix = Path(sys.prefix)
    venv = _show(prefix, root) if sys.prefix != sys.base_prefix else None
    problems: list[str] = []
    active = environ.get("VIRTUAL_ENV")
    if active and not _same_dir(Path(active), prefix):
        problems.append(
            f"VIRTUAL_ENV is {_show(Path(active), root)}, but xpt runs in {_show(prefix, root)}."
        )
    elif (root / ".venv" / "pyvenv.cfg").is_file() and not _same_dir(root / ".venv", prefix):
        problems.append(f"the project has .venv, but xpt runs in {_show(prefix, root)}.")
    wanted = _python_version(root)
    if wanted and version != wanted and not version.startswith(f"{wanted}."):
        problems.append(f".python-version asks for {wanted}, but this is python {version}.")
    return Interpreter(version, _show(Path(sys.executable), root), venv, tuple(problems))


def _python_version(root: Path) -> str | None:
    try:
        text = (root / ".python-version").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line if re.fullmatch(r"\d+(?:\.\d+){0,2}", line) else None
    return None


def _project_names(root: Path, have: Mapping[str, Installed]) -> set[str]:
    """The normalized names of distributions that are the project itself: the name in
    `pyproject.toml`, and any editable install from inside the root."""
    names: set[str] = set()
    try:
        with (root / "pyproject.toml").open("rb") as file:
            name = tomllib.load(file).get("project", {}).get("name")
        if isinstance(name, str):
            names.add(normalize(name))
    except (OSError, tomllib.TOMLDecodeError):
        pass
    for key, item in have.items():
        source = _editable_dir(item.dist)
        if source is not None and (source == root or source.is_relative_to(root)):
            names.add(key)
    return names


def _editable_dir(dist: importlib.metadata.Distribution) -> Path | None:
    try:
        data = json.loads(dist.read_text("direct_url.json") or "null")
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("dir_info", {}).get("editable"):
        return None
    url = urlparse(str(data.get("url", "")))
    if url.scheme != "file":
        return None
    path = unquote(url.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return Path(path).resolve()


def _module_names(directory: Path) -> Iterator[tuple[str, Path]]:
    """The top-level modules `directory` provides on `sys.path`: `name.py` files and
    directories holding `__init__.py`."""
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.suffix == ".py" and entry.is_file():
            name = entry.stem
        elif (entry / "__init__.py").is_file():
            name = entry.name
        else:
            continue
        if name.isidentifier() and name not in _NOT_MODULES:
            yield name, entry


def _searched(root: Path) -> list[Path]:
    """The project root and `sys.path[0]`, when that is another directory outside the
    interpreter's own prefix, such as the directory of a script."""
    if not sys.path:
        return [root]
    first = Path(sys.path[0] or os.getcwd()).resolve()
    prefixes = {Path(p).resolve() for p in (sys.prefix, sys.base_prefix)}
    if not first.is_dir() or first == root.resolve():
        return [root]
    if any(first.is_relative_to(p) for p in prefixes):
        return [root]
    return [root, first]


def shadowing(root: Path, have: Mapping[str, Installed]) -> Iterator[Shadow]:
    """Modules in the project root and `sys.path[0]` named like a standard library module
    or a top-level module of an installed distribution other than the project itself."""
    tops = importlib.metadata.packages_distributions()
    own = _project_names(root, have)
    stdlib = sys.stdlib_module_names - set(sys.builtin_module_names)
    for directory in _searched(root):
        for name, path in _module_names(directory):
            dists = tuple(
                sorted({d for d in tops.get(name, ()) if normalize(d) not in own}, key=str.lower)
            )
            if name in stdlib or dists:
                yield Shadow(_show(path, root), name, name in stdlib, dists)


def _drift(
    root: Path, have: Mapping[str, Installed]
) -> tuple[tuple[str, ...], list[Drift], list[str]]:
    lockfiles: list[str] = []
    drift: list[Drift] = []
    unreadable: list[str] = []
    own = _project_names(root, have)
    for path in lockfile.files(root):
        try:
            pins = lockfile.read(path)
        except lockfile.ERRORS as error:
            unreadable.append(f"{path.name} could not be read: {error}")
            continue
        lockfiles.append(path.name)
        drift.extend(_compare(path.name, pins, have, own))
    return tuple(lockfiles), drift, unreadable


def _compare(
    source: str, pins: Iterable[lockfile.Pin], have: Mapping[str, Installed], own: set[str]
) -> Iterator[Drift]:
    merged: dict[str, lockfile.Pin] = {}
    for pin in pins:
        key = normalize(pin.name)
        before = merged.get(key)
        if before is None:
            merged[key] = pin
        else:
            versions = tuple(dict.fromkeys(before.versions + pin.versions))
            merged[key] = lockfile.Pin(before.name, versions, before.required or pin.required)
    for key, pin in merged.items():
        item = have.get(key)
        if item is None:
            if pin.required and key not in own:
                yield Drift(pin.name, source, pin.versions, None)
        elif pin.versions and not any(lockfile.same_version(item.version, v) for v in pin.versions):
            yield Drift(pin.name, source, pin.versions, item.version)


def lines(found: Report) -> list[str]:
    i = found.interpreter
    where = f"venv: {i.venv}" if i.venv else "no venv"
    if not found.lockfiles:
        lock = "no lockfile"
    else:
        names = " and ".join(found.lockfiles)
        lock = f"differs from {names}" if found.drift else f"matches {names}"
    out = [f"python {i.version} at {i.executable} ({where}, {lock})"]
    out += [f"interpreter: {p}" for p in i.problems]
    out += [f"shadowing: {_shadow_text(s)}" for s in found.shadowing]
    for d in found.drift:
        wants = " or ".join(d.locked) or "it"
        if d.installed is None:
            out.append(f"missing: {d.name} is not installed, {d.lockfile} wants {wants}")
        else:
            out.append(f"mismatch: {d.name} {d.installed} installed, {d.lockfile} wants {wants}")
    out += [f"lockfile: {u}" for u in found.unreadable]
    return out


def _shadow_text(s: Shadow) -> str:
    if s.stdlib:
        return f"{s.path} shadows the standard library module {s.module}"
    return (
        f"{s.path} shadows {s.module} from the installed distribution {', '.join(s.distributions)}"
    )


def to_json(found: Report) -> dict[str, Json]:
    i = found.interpreter
    return {
        "interpreter": _interpreter_json(i, found),
        "shadowing": [
            {
                "path": s.path,
                "module": s.module,
                "stdlib": s.stdlib,
                "distributions": list(s.distributions),
            }
            for s in found.shadowing
        ],
        "drift": [
            {
                "name": d.name,
                "lockfile": d.lockfile,
                "locked": list(d.locked),
                "installed": d.installed,
            }
            for d in found.drift
        ],
        "unreadable": list(found.unreadable),
    }


def _interpreter_json(i: Interpreter, found: Report) -> dict[str, Json]:
    return {
        "version": i.version,
        "executable": i.executable,
        "venv": i.venv,
        "lockfiles": list(found.lockfiles),
        "matches_lock": None if not found.lockfiles else not found.drift,
        "problems": list(i.problems),
    }


def _top_spec(root: Path, name: str) -> ModuleSpec | None:
    """The spec `import name` would find with the project root first on `sys.path`, as
    `python -m` and `python -c` run from the root have it. Finders only locate; nothing
    is imported."""
    for finder in sys.meta_path:
        if finder is importlib.machinery.PathFinder:
            spec = importlib.machinery.PathFinder.find_spec(name, [str(root), *sys.path])
        else:
            find = getattr(finder, "find_spec", None)
            spec = find(name, None) if find is not None else None
        if spec is not None:
            return spec
    return None


def _spec(root: Path, name: str) -> ModuleSpec | None:
    parts = name.split(".")
    if not all(p.isidentifier() for p in parts):
        return None
    spec = _top_spec(root, parts[0])
    for i in range(1, len(parts)):
        if spec is None or not spec.submodule_search_locations:
            return None
        locations = list(spec.submodule_search_locations)
        spec = importlib.machinery.PathFinder.find_spec(".".join(parts[: i + 1]), locations)
    return spec


def _is_stdlib(path: Path) -> bool:
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib"):
        base = Path(paths[key]).resolve()
        if path.is_relative_to(base) and "site-packages" not in path.relative_to(base).parts:
            return True
    return False


def _owner(
    top: str, path: Path, have: Mapping[str, Installed]
) -> tuple[Installed | None, tuple[str, ...]]:
    """The installed distribution whose files include `path`, and every distribution that
    provides the top-level module `top`."""
    claimed = tuple(importlib.metadata.packages_distributions().get(top, ()))
    for name in claimed:
        item = have.get(normalize(name))
        if item is None:
            continue
        for file in item.dist.files or ():
            if Path(str(item.dist.locate_file(file))).resolve() == path:
                return item, claimed
    return _editable_owner(path, have), claimed


def _editable_owner(path: Path, have: Mapping[str, Installed]) -> Installed | None:
    """The editable install whose source directory holds `path` most closely, for a path
    outside any installed package directory."""
    if {"site-packages", "dist-packages"}.intersection(path.parts):
        return None
    best: tuple[int, Installed] | None = None
    for item in have.values():
        source = _editable_dir(item.dist)
        closer = best is None or (source is not None and len(source.parts) > best[0])
        if source is not None and path.is_relative_to(source) and closer:
            best = (len(source.parts), item)
    return best[1] if best else None


def find(root: Path, name: str) -> Found:
    """Where `import name` would come from, located without importing it or its parents."""
    spec = _spec(root, name)
    if spec is None:
        return Found(name, "missing", None, (), False, None, None, None)
    top = name.partition(".")[0]
    if spec.origin in ("built-in", "frozen"):
        kind: Kind = "built-in" if spec.origin == "built-in" else "frozen"
        return Found(name, kind, None, (), top in sys.stdlib_module_names, None, None, None)
    if spec.origin is None or not spec.has_location:
        locations = tuple(_show(Path(p), root) for p in spec.submodule_search_locations or ())
        return Found(name, "namespace", None, locations, False, None, None, None)
    path = Path(spec.origin).resolve()
    stdlib = _is_stdlib(path)
    owner, claimed = _owner(top, path, installed())
    shadows = None
    if top in sys.stdlib_module_names and not stdlib:
        shadows = f"the standard library module {top}"
    elif owner is None and claimed:
        shadows = f"{top} from the installed distribution {', '.join(claimed)}"
    return Found(
        name,
        "file",
        _show(Path(spec.origin), root),
        (),
        stdlib,
        owner.name if owner else None,
        owner.version if owner else None,
        shadows,
    )


def found_lines(f: Found) -> list[str]:
    if f.kind == "missing":
        return [f"{f.name} is not importable here, with the project root first on sys.path"]
    if f.kind in ("built-in", "frozen"):
        return [f"{f.name} is {'built' if f.kind == 'built-in' else 'frozen'} into the interpreter"]
    if f.kind == "namespace":
        return [f"{f.name} is a namespace package in {', '.join(f.locations)}"]
    if f.distribution is None:
        line = f"{f.name} from {f.origin}" + (" (standard library)" if f.stdlib else "")
    elif normalize(f.distribution) == normalize(f.name):
        line = f"{f.name} {f.version} from {f.origin}"
    else:
        line = f"{f.name} ({f.distribution} {f.version}) from {f.origin}"
    return [line] + ([f"shadowing: {f.origin} shadows {f.shadows}"] if f.shadows else [])


def found_json(f: Found) -> dict[str, Json]:
    return {
        "name": f.name,
        "kind": f.kind,
        "origin": f.origin,
        "locations": list(f.locations),
        "stdlib": f.stdlib,
        "distribution": f.distribution,
        "version": f.version,
        "shadows": f.shadows,
    }


def problem(f: Found) -> bool:
    return f.kind == "missing" or f.shadows is not None


def command(
    root: Path, name: str | None, environ: Mapping[str, str]
) -> tuple[int, list[str], dict[str, Json]]:
    """The exit code, lines, and JSON for `xpt env [NAME]`, from the nearest directory up
    from `root` that holds `pyproject.toml` or `.git`."""
    root = project_root(root.resolve())
    if name is not None:
        f = find(root, name)
        interpreter = _interpreter(root, environ)
        data: dict[str, Json] = {
            "interpreter": {
                "version": interpreter.version,
                "executable": interpreter.executable,
                "venv": interpreter.venv,
                "problems": list(interpreter.problems),
            },
            "module": found_json(f),
        }
        return (1 if problem(f) else 0), found_lines(f), data
    found = report(root, environ)
    return (1 if found.has_problems else 0), lines(found), to_json(found)
