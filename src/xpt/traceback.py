"""The pytest report's compact failure shape for uncaught exceptions outside pytest.

`install()` sets `sys.excepthook` and `threading.excepthook` when `XPT_TRACEBACK` asks for
it, and `xpt run -- <command>` sets that variable and puts `startup/` on `PYTHONPATH`, whose
`sitecustomize` installs the same hooks in every Python process the command starts.
"""

from __future__ import annotations

import linecache
import os
import subprocess
import sys
import sysconfig
import threading
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import BuiltinFunctionType, FrameType, FunctionType, MethodType, ModuleType
from types import TracebackType as Traceback
from typing import Any

from xpt import frames
from xpt.failure import Raised
from xpt.report import cut, show
from xpt.serialize import serialize
from xpt.summary import Limits, limits, raised_lines

ENV = "XPT_TRACEBACK"
STARTUP = Path(__file__).with_name("startup")
ISSUES = "https://github.com/Harville-Labs/xpt/issues"
_OFF = ("", "0", "off", "full")
_MODES = {0: "1", 1: "verbose"}
_LONGEST_CYCLE = 8
_HIDDEN_TYPES = (ModuleType, FunctionType, BuiltinFunctionType, MethodType, type)


def verbosity(mode: str | None) -> int | None:
    """The report verbosity an `XPT_TRACEBACK` value asks for, or None for Python's own
    traceback: unset, `0`, `off`, and `full` keep it, `verbose` raises the limits as `-v`
    does under pytest, and any other value prints the default compact traceback."""
    value = (mode or "").strip().lower()
    if value in _OFF:
        return None
    return 1 if value == "verbose" else 0


def project_root(start: Path) -> Path:
    """The nearest directory from `start` up that holds `pyproject.toml` or `.git`, else
    `start` itself."""
    for directory in (start, *start.parents):
        if (directory / "pyproject.toml").is_file() or (directory / ".git").exists():
            return directory
    return start


@dataclass(frozen=True, slots=True)
class Step:
    """One traceback entry: the path as printed, whether it is project code, and its frame."""

    path: str
    line: int | None
    function: str
    project: bool
    frame: FrameType

    @property
    def at(self) -> str:
        return self.path if self.line is None else f"{self.path}:{self.line}"

    @property
    def label(self) -> str:
        return f"{self.at} in {self.function}"


@dataclass(frozen=True, slots=True)
class Hooks:
    """The compact hooks for one process: the project root, the start directory relative
    paths resolve against, and the limits."""

    root: Path
    cwd: Path
    limits: Limits

    @classmethod
    def start(cls, cwd: str | Path, mode: str | None = None) -> Hooks | None:
        """Hooks for a process started in `cwd`, or None when `mode` (by default
        `XPT_TRACEBACK`) keeps Python's traceback."""
        level = verbosity(os.environ.get(ENV) if mode is None else mode)
        if level is None:
            return None
        start = Path(cwd)
        return cls(project_root(start), start, limits(level))

    def excepthook(
        self, kind: type[BaseException], error: BaseException, tb: Traceback | None
    ) -> None:
        stream = sys.stderr
        if stream is None:
            return
        try:
            text = "\n".join(self.lines(error, tb)) + "\n"
        except Exception as failed:
            sys.__excepthook__(kind, error, tb)
            stream.write(notice(failed) + "\n")
            return
        stream.write(text)
        stream.flush()

    def threadhook(self, args: Any) -> None:
        if args.exc_type is SystemExit:
            return
        stream = sys.stderr if sys.stderr is not None else getattr(args.thread, "_stderr", None)
        if stream is None:
            return
        name = args.thread.name if args.thread is not None else threading.get_ident()
        error = args.exc_value if args.exc_value is not None else args.exc_type()
        try:
            text = "\n".join(self.lines(error, args.exc_traceback)) + "\n"
        except Exception as failed:
            stream.write(f"Exception in thread {name}:\n")
            traceback.print_exception(
                args.exc_type, args.exc_value, args.exc_traceback, file=stream
            )
            stream.write(notice(failed) + "\n")
            return
        stream.write(f"Exception in thread {name}:\n{text}")
        stream.flush()

    def lines(self, error: BaseException, tb: Traceback | None) -> list[str]:
        """The compact traceback: `Type: message`, where it was raised, the library it went
        through, its project callers, one line per chained exception, and the locals of the
        deepest project frame."""
        lim = self.limits
        first, *rest = raised_lines(Raised(frames.name(error), _message(error)), lim)
        lines = [first.strip(), *rest]
        trail = self.steps(tb)
        deepest = _deepest(trail)
        syntax = _syntax_at(error, self)
        callers: list[str] = []
        if syntax is not None:
            lines += _at_lines(*syntax, lim)
            callers = [s.label for s in trail if s.project]
        elif deepest is not None:
            step = trail[deepest]
            lines += _at_lines(step.label, _source(step), lim)
            through = next((s for s in trail[deepest + 1 :] if not s.project), None)
            if step.project and through is not None:
                lines.append(f"  via {through.path}")
            callers = [s.label for s in trail[:deepest] if s.project]
        if callers:
            lines.append(f"  from {', '.join(reversed(frames.keep(collapse(callers))))}")
        lines += self.chain_lines(error)
        if syntax is None and deepest is not None and trail[deepest].project:
            lines += locals_lines(trail[deepest].frame, lim)
        return lines

    def chain_lines(self, error: BaseException) -> list[str]:
        """One line per chained exception, then one per member of an exception group."""
        links = frames.chain(error)
        if isinstance(error, BaseExceptionGroup):
            links += [("member", member) for member in error.exceptions]
        limit = len(links) if self.limits.changes is None else self.limits.changes
        lines = []
        for link, linked in links[:limit]:
            text = cut(_one_line(linked), self.limits.message)
            trail = self.steps(linked.__traceback__)
            deepest = _deepest(trail)
            where = "" if deepest is None else f" at {trail[deepest].at}"
            lines.append(f"  {link} {text}{where}")
        if (hidden := len(links) - limit) > 0:
            lines.append(f"  ... {hidden} more")
        return lines

    def steps(self, tb: Traceback | None) -> list[Step]:
        """The traceback's entries outermost first, without frames that set
        `__tracebackhide__`, as pytest leaves them out."""
        steps = []
        while tb is not None:
            frame = tb.tb_frame
            if not _hidden(frame):
                path, project = self.place(frame.f_code.co_filename)
                steps.append(Step(path, tb.tb_lineno, frame.f_code.co_qualname, project, frame))
            tb = tb.tb_next
        return steps

    def place(self, filename: str) -> tuple[str, bool]:
        """How a code file prints, and whether it is project code.

        Project code is under the root, outside installed package directories and the
        running Python's prefixes, or code typed in with `-c` or at a prompt. Anything else
        prints relative to the `sys.path` entry it was imported from, as `pkg/module.py`.
        """
        if filename.startswith("<"):
            return filename, not filename.startswith("<frozen")
        path = Path(filename)
        if not path.is_absolute():
            path = self.cwd / path
        if not _installed(path):
            for candidate, root in ((path, self.root), (path.resolve(), self.root.resolve())):
                if (relative := frames.project_path(candidate, root)) is not None:
                    return relative, True
        return library_path(path), False


def install(mode: str | None = None) -> bool:
    """Print uncaught exceptions in the compact shape, from the main thread and others.

    `mode` defaults to `XPT_TRACEBACK`, so a program can call this unconditionally and
    print Python's traceback unless the variable is set. Returns whether it installed.
    """
    hooks = Hooks.start(Path.cwd(), mode)
    if hooks is None:
        return False
    sys.excepthook = hooks.excepthook
    threading.excepthook = hooks.threadhook
    return True


def run(command: Sequence[str], verbose: int = 0) -> int:
    """Run `command` with compact tracebacks in its Python processes; returns its exit code.

    `verbose` 1 raises the limits and 2 keeps Python's full traceback.
    """
    env = dict(os.environ)
    env[ENV] = _MODES.get(verbose, "full")
    if verbose < 2:
        given = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join([str(STARTUP), *([given] if given else [])])
    try:
        code = subprocess.run(list(command), env=env, check=False).returncode
    except OSError as error:
        print(f"xpt: error: could not run {command[0]}: {error.strerror or error}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130
    return 128 - code if code < 0 else code


def notice(error: BaseException) -> str:
    return (
        f"xpt: the compact traceback failed ({type(error).__name__}: {error}), so this is "
        f"Python's traceback. Please report it at {ISSUES}. XPT_TRACEBACK=full hides this line."
    )


def collapse(labels: Sequence[str], longest: int = _LONGEST_CYCLE) -> list[str]:
    """Fold each run of a repeated frame, or of a repeated cycle of up to `longest` frames,
    into one entry with its count, as recursion leaves them."""
    out: list[str] = []
    i = 0
    while i < len(labels):
        best = (1, 1)
        for period in range(1, longest + 1):
            block = list(labels[i : i + period])
            if len(block) < period:
                break
            times = 1
            while list(labels[i + times * period : i + (times + 1) * period]) == block:
                times += 1
            if times > 1 and times * period > best[0] * best[1]:
                best = (period, times)
        period, times = best
        if times == 1:
            out.append(labels[i])
        else:
            out.append(f"{', '.join(labels[i : i + period])} ({times} times)")
        i += period * times
    return out


def locals_lines(frame: FrameType, lim: Limits) -> list[str]:
    """The frame's locals as compact value trees, leaving out dunders, modules, functions,
    and classes. A value xpt cannot serialize prints as `<module.Type>`."""
    names = visible_locals(frame)
    if not names:
        return []
    limit = len(names) if lim.changes is None else lim.changes
    lines = ["  locals:", *(f"    {n} = {value_text(v, lim)}" for n, v in names[:limit])]
    if (hidden := len(names) - limit) > 0:
        lines.append(f"    ... {hidden} more locals")
    return lines


def visible_locals(frame: FrameType) -> list[tuple[str, object]]:
    """The frame's locals without dunders, modules, functions, and classes."""
    return [
        (name, value)
        for name, value in frame.f_locals.items()
        if not (name.startswith("__") and name.endswith("__"))
        and not isinstance(value, _HIDDEN_TYPES)
    ]


def value_text(value: object, lim: Limits) -> str:
    try:
        return show(serialize(value), lim.width)
    except Exception:
        return placeholder(value)


def placeholder(value: object) -> str:
    """How a value xpt cannot serialize prints: its type as `<module.Type>`."""
    kind = type(value)
    module = "" if kind.__module__ == "builtins" else f"{kind.__module__}."
    return f"<{module}{kind.__qualname__}>"


def library_path(path: Path) -> str:
    """`path` relative to the longest `sys.path` entry holding it, else its file name."""
    best: Path | None = None
    for entry in sys.path:
        base = Path(entry or os.curdir).absolute()
        if path.is_relative_to(base) and (best is None or len(base.parts) > len(best.parts)):
            best = base
    return path.name if best is None else path.relative_to(best).as_posix()


def _installed(path: Path) -> bool:
    """Whether `path` is in the standard library, an installed package, or a script
    directory of the running Python, which can sit inside the project as `.venv/`."""
    paths = sysconfig.get_paths()
    found = (paths.get(key) for key in ("stdlib", "platstdlib", "purelib", "platlib", "scripts"))
    return any(path.is_relative_to(p) for p in found if p)


def _hidden(frame: FrameType) -> bool:
    return bool(frame.f_locals.get("__tracebackhide__") or frame.f_globals.get("__tracebackhide__"))


def _deepest(trail: Sequence[Step]) -> int | None:
    """The index of the deepest project step, else of the deepest step."""
    for i in range(len(trail) - 1, -1, -1):
        if trail[i].project:
            return i
    return len(trail) - 1 if trail else None


def _source(step: Step) -> str:
    filename = step.frame.f_code.co_filename
    return "" if step.line is None else linecache.getline(filename, step.line).strip()


def _at_lines(at: str, source: str, lim: Limits) -> list[str]:
    return [f"  at {at}", *([f"  > {cut(source, lim.width)}"] if source else [])]


def _syntax_at(error: BaseException, hooks: Hooks) -> tuple[str, str] | None:
    """A syntax error's own location and line, which its traceback does not hold."""
    if not isinstance(error, SyntaxError) or not error.filename:
        return None
    path, _ = hooks.place(error.filename)
    at = path if error.lineno is None else f"{path}:{error.lineno}"
    return at, (error.text or "").strip()


def _message(error: BaseException) -> str:
    try:
        return frames.message(error)
    except Exception:
        return "<exception str() failed>"


def _one_line(error: BaseException) -> str:
    try:
        return frames.one_line(error)
    except Exception:
        return f"{frames.name(error)}: <exception str() failed>"
