from __future__ import annotations

import codecs
import configparser
import importlib.metadata
import importlib.resources
import re
import shlex
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xpt.atomic import replace
from xpt.value import Json

FILE = "AGENTS.md"
BEGIN = "<!-- xpt:begin -->"
END = "<!-- xpt:end -->"
INSTRUCTIONS = (
    importlib.resources.files("xpt").joinpath("instructions.md").read_text(encoding="utf-8")
)
PLUGIN = "xpt.pytest_plugin"

_HEADING = INSTRUCTIONS.partition("\n")[0]
_MIN_PYTEST = (8, 0)
_DISABLED = frozenset({"no:xpt", f"no:{PLUGIN}"})
_INI_FILES = (("pytest.ini", "pytest"), (".pytest.ini", "pytest"), ("pyproject.toml", ""))
_CFG_FILES = (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest"))

Status = Literal["created", "added", "present", "kept"]


@dataclass(frozen=True, slots=True)
class Probe:
    """What the interpreter running xpt has installed."""

    pytest_version: str | None
    plugin_installed: bool
    executable: str


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class InitResult:
    """What init did to AGENTS.md, why when it left the block alone, and the pytest checks."""

    status: Status
    note: str
    checks: tuple[Check, ...]


class InitError(Exception):
    pass


def probe() -> Probe:
    try:
        version = importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError:
        version = None
    installed = bool(importlib.metadata.entry_points(group="pytest11", name="xpt"))
    return Probe(version, installed, sys.executable)


def init(root: Path, found: Probe, env: Mapping[str, str], *, dry_run: bool = False) -> InitResult:
    """Add the agent instructions to `root`/AGENTS.md between markers, and check pytest.

    An existing block, marked or pasted by hand, is never changed. A dry run reports the same
    result and writes nothing.
    """
    file = root / FILE
    status, note, data = _plan(file)
    if data is not None and not dry_run:
        try:
            replace(file.resolve(), data)
        except OSError as error:
            raise InitError(f"{FILE} could not be written: {error.strerror}.") from None
    return InitResult(status, note, check(root, found, env))


def _plan(file: Path) -> tuple[Status, str, bytes | None]:
    block = f"{BEGIN}\n{INSTRUCTIONS}{END}\n"
    if not file.exists():
        return "created", "", block.encode("utf-8")
    try:
        raw = file.read_bytes()
    except OSError as error:
        raise InitError(f"{FILE} could not be read: {error.strerror}.") from None
    bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
    try:
        text = raw[len(bom) :].decode("utf-8")
    except UnicodeDecodeError as error:
        raise InitError(
            f"{FILE} is not valid UTF-8 ({error.reason} at byte {error.start})."
        ) from None
    if BEGIN in text or END in text:
        return _marked(text)
    if any(line.strip() == _HEADING for line in text.splitlines()):
        note = (
            f'{FILE} already has a "{_HEADING.lstrip("# ")}" section without xpt\'s markers, so '
            "it was left as it is. To let xpt init manage it, delete it and run xpt init again."
        )
        return "kept", note, None
    first = re.search(r"\r\n|\r|\n", text)
    newline = first.group() if first else "\n"
    if not text or text.endswith(newline * 2):
        gap = ""
    else:
        gap = newline if text.endswith(newline) else newline * 2
    new = text + gap + block.replace("\n", newline)
    return "added", "", bom + new.encode("utf-8")


def _marked(text: str) -> tuple[Status, str, None]:
    start, end = text.find(BEGIN), text.find(END)
    if text.count(BEGIN) != 1 or text.count(END) != 1 or end < start:
        raise InitError(
            f"{FILE} has unmatched xpt markers. It needs one {BEGIN} line before one {END} "
            "line. Fix or delete the markers and run xpt init again."
        )
    inner = text[start + len(BEGIN) : end].replace("\r\n", "\n").replace("\r", "\n")
    if inner.strip() == INSTRUCTIONS.strip():
        return "present", "", None
    note = (
        f"The xpt block in {FILE} differs from this version of xpt and was left as it is. "
        "To replace it, delete it, markers included, and run xpt init again."
    )
    return "kept", note, None


def check(root: Path, found: Probe, env: Mapping[str, str]) -> tuple[Check, Check]:
    return _pytest(found), _plugin(root, found, env)


def _pytest(found: Probe) -> Check:
    where = found.executable
    version = found.pytest_version
    if version is None:
        return Check(
            "pytest",
            False,
            f"pytest is not installed in {where}. Install it in the project's environment, "
            "for example uv add --dev pytest xpt.",
        )
    parts = re.match(r"(\d+)\.(\d+)", version)
    if parts and (int(parts[1]), int(parts[2])) < _MIN_PYTEST:
        minimum = ".".join(map(str, _MIN_PYTEST))
        return Check(
            "pytest",
            False,
            f"pytest {version} in {where} is older than {minimum}, which xpt needs.",
        )
    return Check("pytest", True, f"pytest {version} in {where}")


def _plugin(root: Path, found: Probe, env: Mapping[str, str]) -> Check:
    def problem(detail: str) -> Check:
        return Check("plugin", False, detail)

    config = _config(root)
    if isinstance(config, str):
        return problem(config)
    name, addopts = config
    try:
        env_opts = shlex.split(env.get("PYTEST_ADDOPTS", ""))
    except ValueError as error:
        return problem(f"PYTEST_ADDOPTS could not be read ({error}).")
    for source, opts in ((f"addopts in {name}", addopts), ("PYTEST_ADDOPTS", env_opts)):
        if _DISABLED & _plugins(opts):
            return problem(f"{source} passes -p no:xpt, which disables xpt's plugin.")
    if not found.plugin_installed:
        return problem(
            f"xpt's pytest plugin is not installed in {found.executable}. Install xpt in the "
            "environment that runs pytest, for example uv add --dev xpt."
        )
    named = PLUGIN in _plugins([*addopts, *env_opts]) or PLUGIN in env.get(
        "PYTEST_PLUGINS", ""
    ).split(",")
    if env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") and not named:
        return problem(
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD is set, so pytest loads no installed plugins. "
            f"Add -p {PLUGIN} to addopts."
        )
    where = f"Configuration: {name}." if name else "No pytest configuration found."
    return Check("plugin", True, f"pytest loads xpt's plugin. {where}")


def _plugins(opts: list[str]) -> set[str]:
    found = set()
    for i, opt in enumerate(opts):
        if opt == "-p" and i + 1 < len(opts):
            found.add(opts[i + 1])
        elif opt.startswith("-p") and len(opt) > 2:
            found.add(opt[2:])
    return found


def _config(root: Path) -> tuple[str | None, list[str]] | str:
    """The pytest configuration file in `root` that pytest would use, and its addopts."""
    for name, section in (*_INI_FILES, *_CFG_FILES):
        file = root / name
        if not file.is_file():
            continue
        try:
            addopts = _addopts(file, section, required=(name, section) in _CFG_FILES)
        except (OSError, ValueError, configparser.Error) as error:
            return f"{name} could not be read ({error})."
        if addopts is not None:
            return name, addopts
    return None, []


def _addopts(file: Path, section: str, *, required: bool) -> list[str] | None:
    if file.suffix == ".toml":
        tool = tomllib.loads(file.read_text(encoding="utf-8")).get("tool", {})
        if "ini_options" in tool.get("pytest", {}):
            return _split(tool["pytest"]["ini_options"].get("addopts", []))
        if "pytest" in tool:
            return _split(tool["pytest"].get("addopts", []))
        return None
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(file.read_text(encoding="utf-8"))
    if not parser.has_section(section):
        return None if required else []
    return _split(parser.get(section, "addopts", fallback=""))


def _split(addopts: str | list[str]) -> list[str]:
    return shlex.split(addopts) if isinstance(addopts, str) else [str(a) for a in addopts]


def lines(result: InitResult, dry_run: bool) -> list[str]:
    said = {
        "created": f"Created {FILE} with the xpt block.",
        "added": f"Added the xpt block to {FILE}.",
        "present": f"{FILE} already has the xpt block.",
        "kept": result.note,
    }[result.status]
    if dry_run:
        said = {
            "created": f"Dry run, would create {FILE} with the xpt block.",
            "added": f"Dry run, would add the xpt block to {FILE}.",
        }.get(result.status, said)
    out = [said, *(f"{'ok' if c.ok else 'problem'}  {c.name}  {c.detail}" for c in result.checks)]
    if all(c.ok for c in result.checks):
        return [*out, "Next: run pytest."]
    return [*out, "Fix the problems above and run xpt init again."]


def to_json(result: InitResult, dry_run: bool) -> dict[str, Json]:
    return {
        "dry_run": dry_run,
        "path": FILE,
        "status": result.status,
        "note": result.note,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in result.checks],
    }
