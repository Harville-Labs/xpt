"""`xpt probe FILE:LINE -- command`: record the values at one line without editing source.

The command runs with `startup/` first on `PYTHONPATH` and `XPT_PROBE` set, so each
Python process it starts loads the line tracer in `startup/_xpt_probe.py`. That tracer
imports `observe()` from here on the first hit, records the frame's locals (or `--expr`) as
value trees with the project call chain, and appends them to a file of its own. When the
command ends, the files are merged into `.xpt/probes/<id>.jsonl` and summarized. The
merged file is read back as data: values are rebuilt with `from_json`, never evaluated.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, FrameType
from typing import Any, NoReturn

from xpt import frames
from xpt.diff import diff
from xpt.path import format_path
from xpt.record import SCHEMA_VERSION, new_run_id, state_directory
from xpt.report import MAX_CHANGES, MAX_CHARS, cut, show
from xpt.serialize import serialize
from xpt.summary import limits
from xpt.traceback import STARTUP, Hooks, collapse, placeholder, project_root, visible_locals
from xpt.value import InvalidValue, Json, Value, from_json, to_json

ENV = "XPT_PROBE"
TRACER_ENV = "XPT_PROBE_TRACER"
PROBES = "probes"
KEEP_PROBES = 5
MAX_HITS = 100
MAX_VALUE_CHARS = 100_000
USAGE = "xpt probe FILE:LINE [--expr EXPR] [--when COND] [--max-hits N] [--json] -- command"


class ProbeError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Target:
    file: Path
    line: int
    when: str | None = None


@dataclass(frozen=True, slots=True)
class Named:
    """One recorded name: its value tree, the type of a value xpt cannot serialize, or the
    exception `--expr` raised."""

    name: str
    value: Value | None = None
    unsupported: str | None = None
    error: str | None = None

    def shown(self, width: int | None = MAX_CHARS) -> str:
        if self.value is not None:
            return show(self.value, width)
        return cut(self.unsupported or f"<raised {self.error}>", width)


@dataclass(frozen=True, slots=True)
class Hit:
    pid: int
    seq: int
    function: str
    chain: tuple[str, ...]
    values: tuple[Named, ...]


@dataclass(frozen=True, slots=True)
class Process:
    pid: int
    reached: int
    hits: int
    when_errors: int
    when_error: str | None
    error: str | None
    tracer: str | None


@dataclass(frozen=True, slots=True)
class Probed:
    """A merged probe file: what was probed, every recorded hit, and each process's counts."""

    path: str
    line: int
    expr: str | None
    when: str | None
    max_hits: int
    exit_code: int
    hits: tuple[Hit, ...]
    processes: tuple[Process, ...]

    @property
    def reached(self) -> int:
        return sum(p.reached for p in self.processes)

    @property
    def total(self) -> int:
        """Hits counted in every process, including those past `max_hits`; a process that
        exited without writing its counts counts its recorded hits."""
        counted = {p.pid: p.hits for p in self.processes}
        for h in self.hits:
            counted[h.pid] = max(counted.get(h.pid, 0), h.seq)
        return sum(counted.values())


# ---- in the probed process ----------------------------------------------------------------

_hooks: dict[str, Hooks] = {}


def observe(frame: FrameType, expr: CodeType | None, expr_text: str | None, root: str) -> Json:
    """One hit of the probed line: its function, the project call chain outermost first,
    and the frame's locals, or `expr` evaluated in the frame, as value trees."""
    if expr is None or expr_text is None:
        values = [_named(name, value) for name, value in visible_locals(frame)]
    else:
        try:
            values = [_named(expr_text, eval(expr, frame.f_globals, frame.f_locals))]
        except Exception as error:
            values = [{"name": expr_text, "error": frames.one_line(error)}]
    return {
        "function": frame.f_code.co_qualname,
        "chain": _chain(frame, root),
        "values": values,
    }


def _named(name: str, value: object) -> dict[str, Json]:
    try:
        tree = to_json(serialize(value))
    except Exception:
        return {"name": name, "unsupported": placeholder(value)}
    size = len(json.dumps(tree, ensure_ascii=False))
    if size > MAX_VALUE_CHARS:
        return {"name": name, "unsupported": f"{placeholder(value)[:-1]}, {size} chars>"}
    return {"name": name, "value": tree}


def _chain(frame: FrameType, root: str) -> list[Json]:
    hooks = _hooks.get(root)
    if hooks is None:
        hooks = _hooks[root] = Hooks(Path(root), Path.cwd(), limits(0))
    chain: list[Json] = []
    caller = frame.f_back
    while caller is not None:
        path, project = hooks.place(caller.f_code.co_filename)
        if project:
            chain.append(
                {"path": path, "line": caller.f_lineno, "function": caller.f_code.co_qualname}
            )
        caller = caller.f_back
    return chain[::-1]


# ---- in xpt probe -------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ProbeError(f"{message}. Run xpt probe --help for usage.")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="xpt probe",
        usage=USAGE,
        description="Run a command and record the values at one line of a Python file, "
        "without editing the source or starting a debugger.",
    )
    parser.add_argument(
        "target", metavar="FILE:LINE", help="the line, optionally followed by 'if COND'"
    )
    parser.add_argument("--expr", help="record this expression instead of the locals")
    parser.add_argument("--when", help="record only hits where this condition is true")
    parser.add_argument(
        "--max-hits",
        type=int,
        default=MAX_HITS,
        metavar="N",
        help=f"record the first N hits in each process (default: {MAX_HITS})",
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object on stdout")
    return parser


def main(argv: Sequence[str]) -> int:
    argv = list(argv)
    split = argv.index("--") if "--" in argv else len(argv)
    options, command = argv[:split], argv[split + 1 :]
    try:
        args = _parser().parse_args(options)
        if not command:
            raise ProbeError(f"xpt probe needs a command after --, as in {USAGE}.")
        if args.max_hits < 1:
            raise ProbeError("--max-hits must be 1 or more.")
        target = parse_target(args.target)
        when = args.when if target.when is None else target.when
        if args.when is not None and target.when is not None:
            raise ProbeError("give the condition once, with --when or after 'if' in FILE:LINE.")
        return probe(target.file, target.line, command, args.expr, when, args.max_hits, args.json)
    except ProbeError as error:
        print(f"xpt: error: {error}", file=sys.stderr)
        return 2


def parse_target(text: str) -> Target:
    where, _, when = text.partition(" if ")
    file, colon, line = where.strip().rpartition(":")
    if not colon or not file or not line.isdigit() or int(line) < 1:
        raise ProbeError(f"{text!r} is not FILE:LINE, as in src/app/parse.py:42.")
    return Target(Path(file), int(line), when.strip() or None)


def executable_lines(file: Path) -> set[int]:
    """The lines of `file` that hold code, from every code object compiling it makes."""
    try:
        source = file.read_bytes()
    except OSError as error:
        raise ProbeError(f"cannot read {file}: {error.strerror or error}.") from None
    try:
        code = compile(source, str(file), "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as error:
        raise ProbeError(f"{file} does not compile: {error}.") from None
    lines: set[int] = set()
    todo = [code]
    while todo:
        current = todo.pop()
        lines.update(line for _, _, line in current.co_lines() if line is not None)
        todo.extend(c for c in current.co_consts if isinstance(c, CodeType))
    return lines


def _check(file: Path, line: int, shown: str) -> None:
    if not file.is_file():
        raise ProbeError(f"there is no file {shown}.")
    lines = executable_lines(file)
    if line in lines:
        return
    before = max((n for n in lines if n < line), default=None)
    after = min((n for n in lines if n > line), default=None)
    near = " and ".join(str(n) for n in (before, after) if n is not None)
    hint = f" The nearest executable lines are {near}." if near else ""
    raise ProbeError(
        f"{shown}:{line} is not an executable line: it is blank, a comment, or past the end "
        f"of the file, so it can never be hit.{hint}"
    )


def _compiles(text: str | None, option: str) -> None:
    if text is None:
        return
    try:
        compile(text, f"<xpt probe {option}>", "eval")
    except SyntaxError as error:
        raise ProbeError(f"{option} {text!r} is not a Python expression: {error.msg}.") from None


def probe(
    file: Path,
    line: int,
    command: Sequence[str],
    expr: str | None = None,
    when: str | None = None,
    max_hits: int = MAX_HITS,
    as_json: bool = False,
) -> int:
    """Run `command` with the probe on `file:line`, print the summary, and return the
    command's exit code."""
    cwd = Path.cwd()
    root = project_root(cwd)
    absolute = (cwd / file).resolve()
    shown = frames.project_path(absolute, root.resolve()) or file.as_posix()
    _check(absolute, line, shown)
    _compiles(expr, "--expr")
    _compiles(when, "--when")
    directory = state_directory(root) / PROBES
    directory.mkdir(exist_ok=True)
    id = new_run_id()
    parts = directory / f"{id}.d"
    config = {
        "file": str(absolute),
        "line": line,
        "expr": expr,
        "when": when,
        "max_hits": max_hits,
        "out": str(parts),
        "root": str(root),
        "home": str(Path(__file__).resolve().parent.parent),
        "tracer": os.environ.get(TRACER_ENV, "auto"),
    }
    env = dict(os.environ)
    env[ENV] = json.dumps(config)
    given = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join([str(STARTUP), *([given] if given else [])])
    code = _run(command, env, as_json)
    header: dict[str, Json] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "probe",
        "id": id,
        "path": shown,
        "line": line,
        "expr": expr,
        "when": when,
        "max_hits": max_hits,
        "command": list(command),
        "exit_code": code,
    }
    merged = merge(parts, directory / f"{id}.jsonl", header)
    _prune(directory)
    probed = load(merged)
    where = merged.relative_to(root).as_posix() if merged.is_relative_to(root) else str(merged)
    if as_json:
        print(json.dumps(to_data(probed, where), ensure_ascii=False, allow_nan=False))
    else:
        sys.stderr.write("\n".join(lines(probed, where)) + "\n")
        sys.stderr.flush()
    return code


def _run(command: Sequence[str], env: dict[str, str], as_json: bool) -> int:
    """Run the command; under --json its stdout goes to stderr, so stdout holds only the
    JSON object."""
    stdout: Any = None
    if as_json:
        sys.stdout.flush()
        try:
            stdout = sys.stderr.fileno()
        except (AttributeError, OSError, ValueError):
            stdout = subprocess.PIPE
    try:
        done = subprocess.run(list(command), env=env, stdout=stdout, check=False)
    except OSError as error:
        print(f"xpt: error: could not run {command[0]}: {error.strerror or error}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130
    if isinstance(done.stdout, bytes):
        sys.stderr.write(done.stdout.decode("utf-8", "replace"))
    return 128 - done.returncode if done.returncode < 0 else done.returncode


def merge(parts: Path, target: Path, header: dict[str, Json]) -> Path:
    """Merge each process's file into one JSON Lines file, hits in time order, and remove
    the per-process files. A line a killed process left half written is dropped."""
    hits: list[dict[str, Any]] = []
    processes: list[dict[str, Any]] = []
    for part in sorted(parts.glob("*.jsonl")) if parts.is_dir() else []:
        for text in part.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                (hits if record.get("kind") == "hit" else processes).append(record)
    hits.sort(key=lambda h: (h.get("time", 0), h.get("pid", 0), h.get("seq", 0)))
    with target.open("w", encoding="utf-8", newline="\n") as out:
        for record in [header, *hits, *processes]:
            out.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    shutil.rmtree(parts, ignore_errors=True)
    return target


def _prune(directory: Path) -> None:
    for old in sorted(directory.glob("*.jsonl"))[:-KEEP_PROBES]:
        old.unlink(missing_ok=True)


def load(path: Path) -> Probed:
    """Read a merged probe file back as data, naming the line of anything malformed."""
    header: dict[str, Any] | None = None
    hits: list[Hit] = []
    processes: list[Process] = []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(text)
            kind = record["kind"]
            if kind == "probe":
                header = record
            elif kind == "hit":
                hits.append(_hit(record))
            elif kind == "process":
                processes.append(_process(record))
            else:
                raise ValueError(f"unknown kind {kind!r}")
        except (ValueError, KeyError, TypeError, InvalidValue) as error:
            raise ProbeError(f"{path}:{number} is not a probe record: {error}") from None
    if header is None:
        raise ProbeError(f"{path} has no probe header line.")
    return Probed(
        path=str(header["path"]),
        line=int(header["line"]),
        expr=header.get("expr"),
        when=header.get("when"),
        max_hits=int(header["max_hits"]),
        exit_code=int(header["exit_code"]),
        hits=tuple(hits),
        processes=tuple(processes),
    )


def _hit(record: dict[str, Any]) -> Hit:
    chain = tuple(f"{c['path']}:{int(c['line'])} in {c['function']}" for c in record["chain"])
    values = []
    for v in record["values"]:
        name = str(v["name"])
        if "value" in v:
            values.append(Named(name, value=from_json(v["value"])))
        elif "unsupported" in v:
            values.append(Named(name, unsupported=str(v["unsupported"])))
        else:
            values.append(Named(name, error=str(v["error"])))
    return Hit(
        int(record["pid"]), int(record["seq"]), str(record["function"]), chain, tuple(values)
    )


def _process(record: dict[str, Any]) -> Process:
    return Process(
        int(record["pid"]),
        int(record["reached"]),
        int(record["hits"]),
        int(record["when_errors"]),
        record.get("when_error"),
        record.get("error"),
        record.get("tracer"),
    )


def changed(before: Hit, after: Hit, width: int | None = MAX_CHARS) -> list[str]:
    """The paths whose values differ between two hits, as `path: old -> new`."""
    old = {n.name: n for n in before.values}
    out: list[str] = []
    for new in after.values:
        was = old.pop(new.name, None)
        if was is None:
            out.append(f"{new.name}: added {new.shown(width)}")
        elif was.value is not None and new.value is not None:
            for c in diff(was.value, new.value).changes:
                at = format_path(c.path, new.name)
                if c.expected is None and c.actual is not None:
                    out.append(f"{at}: added {show(c.actual, width)}")
                elif c.actual is None and c.expected is not None:
                    out.append(f"{at}: removed {show(c.expected, width)}")
                elif c.expected is not None and c.actual is not None:
                    out.append(f"{at}: {show(c.expected, width)} -> {show(c.actual, width)}")
        elif was.shown(None) != new.shown(None):
            out.append(f"{new.name}: {was.shown(width)} -> {new.shown(width)}")
    out += [f"{name}: removed" for name in old]
    return out


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def _from(chain: Sequence[str]) -> str:
    return ", ".join(reversed(frames.keep(collapse(list(chain)))))


def lines(probed: Probed, where: str, limit: int = MAX_CHANGES) -> list[str]:
    """The text summary: how often the line was hit, the first hit's values, and for each
    later hit only what changed since the hit before it."""
    at = f"{probed.path}:{probed.line}"
    out: list[str] = []
    total = probed.total
    count = len({p.pid for p in probed.processes} | {h.pid for h in probed.hits})
    if total == 0:
        out.append(_never(probed, at, count))
    else:
        head = f"{at} hit {_times(total)}"
        if count > 1:
            head += f" in {count} processes"
        if len(probed.hits) < total:
            head += f", recorded {len(probed.hits)}"
        out.append(head + (f", --expr {probed.expr}" if probed.expr else ""))
    out += _notes(probed)
    previous: Hit | None = None
    for n, hit in enumerate(probed.hits[: limit + 1], 1):
        chain = f" from {_from(hit.chain)}" if hit.chain else ""
        if previous is None:
            out.append(f"  hit 1 in {hit.function}{chain}:")
            out += [f"    {v.name} = {v.shown()}" for v in hit.values] or ["    (no locals)"]
        else:
            moved = chain if hit.chain != previous.chain or hit.pid != previous.pid else ""
            diffs = changed(previous, hit)
            shown = diffs[:limit] + ([f"... {len(diffs) - limit} more"] if diffs[limit:] else [])
            out.append(f"  hit {n}{moved}: {'; '.join(shown) if shown else 'same'}")
        previous = hit
    if (hidden := len(probed.hits) - (limit + 1)) > 0:
        out.append(f"  ... {hidden} more recorded hits")
    out.append(f"Recorded in {where}. Exit code {probed.exit_code}.")
    return out


def _never(probed: Probed, at: str, count: int) -> str:
    if count == 0:
        return (
            f"{at} was never hit, and no Python process loaded the probe. The command must "
            "run Python without -I or -E, which ignore PYTHONPATH."
        )
    processes = f"in {count} Python process{'es' if count > 1 else ''}"
    if probed.when and probed.reached:
        return f"{at} was reached {_times(probed.reached)} {processes}, but --when was never true."
    return f"{at} was never hit {processes}."


def _notes(probed: Probed) -> list[str]:
    out = []
    when_errors = sum(p.when_errors for p in probed.processes)
    if when_errors:
        first = next(p.when_error for p in probed.processes if p.when_error)
        hits = "hit" if when_errors == 1 else "hits"
        out.append(f"  --when raised {first} on {when_errors} {hits}, counted as false")
    out += [f"  process {p.pid}: {p.error}" for p in probed.processes if p.error]
    return out


def to_data(probed: Probed, where: str) -> dict[str, Json]:
    when_errors = sum(p.when_errors for p in probed.processes)
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "probe",
        "file": where,
        "path": probed.path,
        "line": probed.line,
        "expr": probed.expr,
        "when": probed.when,
        "max_hits": probed.max_hits,
        "exit_code": probed.exit_code,
        "hits": probed.total,
        "reached": probed.reached,
        "processes": len({p.pid for p in probed.processes} | {h.pid for h in probed.hits}),
        "when_errors": when_errors,
        "tracers": sorted({p.tracer for p in probed.processes if p.tracer}),
        "errors": [p.error for p in probed.processes if p.error],
        "records": [_hit_data(h) for h in probed.hits],
    }


def _hit_data(hit: Hit) -> dict[str, Json]:
    values: list[Json] = []
    for v in hit.values:
        if v.value is not None:
            values.append({"name": v.name, "value": to_json(v.value)})
        elif v.unsupported is not None:
            values.append({"name": v.name, "unsupported": v.unsupported})
        else:
            values.append({"name": v.name, "error": v.error})
    return {
        "pid": hit.pid,
        "hit": hit.seq,
        "function": hit.function,
        "chain": list(hit.chain),
        "values": values,
    }
