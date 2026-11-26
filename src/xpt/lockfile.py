"""Pinned versions from `uv.lock`, `poetry.lock`, and `requirements*.txt`, read with the
standard library."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NAMES = ("uv.lock", "poetry.lock")
ERRORS = (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError)
_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==(?!=)\s*([^\s;#\\]+)\s*(;[^#\\]*)?"
)
_RELEASE = re.compile(r"\d+(?:\.\d+)*")


def normalize(name: str) -> str:
    """A distribution name in PEP 503 form, as lockfiles and the index compare them."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True, slots=True)
class Pin:
    """A locked package: its versions (more than one when the lock forks by platform), and
    whether every environment needs it, so its absence is drift."""

    name: str
    versions: tuple[str, ...]
    required: bool


def files(root: Path) -> list[Path]:
    """The lockfiles in `root`, in the order they are reported."""
    found = [root / name for name in NAMES] + sorted(root.glob("requirements*.txt"))
    return [p for p in found if p.is_file()]


def same_version(a: str, b: str) -> bool:
    """Equal versions, with `1.0` and `1.0.0` equal as PEP 440 has them."""
    a, b = a.strip().lower().removeprefix("v"), b.strip().lower().removeprefix("v")
    if _RELEASE.fullmatch(a) and _RELEASE.fullmatch(b):
        return _release(a) == _release(b)
    return a == b


def _release(version: str) -> list[int]:
    parts = [int(p) for p in version.split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return parts


def read(path: Path) -> list[Pin]:
    if path.name == "uv.lock":
        return _uv(_toml(path))
    if path.name == "poetry.lock":
        return _poetry(_toml(path))
    return _requirements(path.read_text(encoding="utf-8"))


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def _packages(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    packages = data.get("package", [])
    if not isinstance(packages, list):
        raise ValueError("[[package]] is not a list of tables")
    return [p for p in packages if isinstance(p, dict) and isinstance(p.get("name"), str)]


def _uv(data: Mapping[str, Any]) -> list[Pin]:
    """Every locked package. Required are those reachable from the project through
    dependency edges with no marker, extras included; groups are not followed."""
    packages = _packages(data)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for p in packages:
        by_name.setdefault(normalize(p["name"]), []).append(p)

    def local(p: Mapping[str, Any], here: bool) -> bool:
        source = p.get("source", {})
        if not isinstance(source, dict):
            return False
        return any(source.get(k) == "." if here else k in source for k in ("editable", "virtual"))

    roots = [p for p in packages if local(p, True)] or [p for p in packages if local(p, False)]
    required: set[str] = set()
    todo: list[tuple[str, tuple[str, ...]]] = [(normalize(p["name"]), ()) for p in roots]
    seen: set[tuple[str, tuple[str, ...]]] = set()
    while todo:
        key, extras = todo.pop()
        if (key, extras) in seen:
            continue
        seen.add((key, extras))
        required.add(key)
        for p in by_name.get(key, []):
            edges = list(p.get("dependencies", []))
            optional = p.get("optional-dependencies", {})
            for extra in extras:
                edges.extend(optional.get(extra, []) if isinstance(optional, dict) else [])
            for edge in edges:
                named = isinstance(edge, dict) and isinstance(edge.get("name"), str)
                if named and "marker" not in edge:
                    todo.append((normalize(edge["name"]), tuple(edge.get("extra", ()))))
    pins: list[Pin] = []
    for p in packages:
        version = p.get("version")
        versions = (version,) if isinstance(version, str) else ()
        pins.append(Pin(p["name"], versions, normalize(p["name"]) in required))
    return pins


def _poetry(data: Mapping[str, Any]) -> list[Pin]:
    """Every locked package. Required are main-group packages that are not optional and
    carry no markers."""
    pins: list[Pin] = []
    for p in _packages(data):
        version = p.get("version")
        groups = p.get("groups", [p.get("category")])
        required = (
            "main" in (groups if isinstance(groups, list) else [])
            and not p.get("optional", False)
            and "markers" not in p
        )
        pins.append(Pin(p["name"], (version,) if isinstance(version, str) else (), required))
    return pins


def _requirements(text: str) -> list[Pin]:
    """Each `name==version` line. Required are those without an environment marker."""
    pins: list[Pin] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQUIREMENT.match(line)
        if match and "*" not in match.group(2):
            pins.append(Pin(match.group(1), (match.group(2),), match.group(3) is None))
    return pins
