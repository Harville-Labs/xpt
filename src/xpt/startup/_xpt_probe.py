"""The tracer behind `xpt probe`, loaded by xpt's `sitecustomize` when `XPT_PROBE` is set.

It uses only the standard library until the probed line runs, so a process that never
reaches it pays for this file and for one callback per code object it starts. On Python
3.12 and later it uses `sys.monitoring`: a `PY_START` callback sees each code object once,
turns on `LINE` events for the ones in the target file that hold the line, and disables
itself everywhere else, and the `LINE` callback disables every other line. On 3.11 it uses
`sys.settrace` and traces lines only in frames of those code objects.

Each process appends its hits as JSON Lines to its own file in the directory the
`XPT_PROBE` configuration names, and `xpt probe` merges the files when the command ends.
"""

import _thread
import json
import os
import sys
import time


def _real(filename):
    if not filename or filename.startswith("<"):
        return None
    try:
        return os.path.normcase(os.path.realpath(filename))
    except (OSError, ValueError):
        return None


def _one_line(error):
    text = " ".join(str(error).split())
    name = type(error).__qualname__
    return f"{name}: {text}" if text else name


class Probe:
    def __init__(self, config):
        self.target = _real(config["file"])
        self.line = int(config["line"])
        self.max_hits = int(config["max_hits"])
        self.out = config["out"]
        self.root = config["root"]
        self.home = config.get("home")
        self.expr_text = config.get("expr")
        self.when_text = config.get("when")
        self.expr = self._compile(self.expr_text, "--expr")
        self.when = self._compile(self.when_text, "--when")
        self.files = {}
        self.codes = {}
        self.lock = _thread.allocate_lock()
        self.busy = set()
        self.observe = None
        self.tracer = None
        self._reset()

    @staticmethod
    def _compile(text, option):
        return None if text is None else compile(text, f"<xpt probe {option}>", "eval")

    def _reset(self):
        self.pid = os.getpid()
        self.reached = 0
        self.hits = 0
        self.recorded = 0
        self.when_errors = 0
        self.when_error = None
        self.error = None
        self.file = None

    def wants(self, code):
        """Whether `code` is from the target file and holds the target line."""
        name = code.co_filename
        match = self.files.get(name)
        if match is None:
            match = self.files[name] = self.target is not None and _real(name) == self.target
        if not match:
            return False
        found = self.codes.get(code)
        if found is None:
            found = self.codes[code] = any(line == self.line for _, _, line in code.co_lines())
        return found

    def hit(self, frame):
        thread = _thread.get_ident()
        if thread in self.busy:
            return
        self.busy.add(thread)
        try:
            self._hit(frame)
        finally:
            self.busy.discard(thread)

    def _hit(self, frame):
        self.reached += 1
        if self.when is not None:
            try:
                if not eval(self.when, frame.f_globals, frame.f_locals):
                    return
            except Exception as error:
                self.when_errors += 1
                if self.when_error is None:
                    self.when_error = _one_line(error)
                return
        self.hits += 1
        if self.hits > self.max_hits or self.error is not None:
            return
        observe = self._observer()
        if observe is None:
            return
        try:
            record = observe(frame, self.expr, self.expr_text, self.root)
        except Exception as error:
            self.error = f"recording a hit failed: {_one_line(error)}"
            return
        self.recorded += 1
        record = {
            "kind": "hit",
            "pid": self.pid,
            "seq": self.hits,
            "time": time.time_ns(),
            **record,
        }
        self._write(record)

    def _observer(self):
        if self.observe is None:
            try:
                from xpt.probe import observe
            except ImportError:
                if self.home and self.home not in sys.path:
                    sys.path.append(self.home)
                try:
                    from xpt.probe import observe
                except Exception as error:
                    self.error = f"this Python could not import xpt: {_one_line(error)}"
                    return None
            self.observe = observe
        return self.observe

    def _write(self, record):
        text = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        with self.lock:
            if self.file is None:
                os.makedirs(self.out, exist_ok=True)
                name = f"{self.pid}-{os.urandom(3).hex()}.jsonl"
                self.file = open(  # noqa: SIM115  # kept open for the life of the process
                    os.path.join(self.out, name), "a", encoding="utf-8", newline="\n"
                )
            self.file.write(text)
            self.file.flush()

    def finish(self):
        """Write this process's counts, which hits past `max_hits` only add to."""
        if self.pid != os.getpid():
            return
        try:
            self._write(
                {
                    "kind": "process",
                    "pid": self.pid,
                    "executable": sys.executable,
                    "tracer": self.tracer,
                    "reached": self.reached,
                    "hits": self.hits,
                    "recorded": self.recorded,
                    "when_errors": self.when_errors,
                    "when_error": self.when_error,
                    "error": self.error,
                }
            )
            if self.file is not None:
                self.file.close()
        except Exception:
            pass

    def install(self, tracer):
        if tracer != "settrace" and hasattr(sys, "monitoring"):
            self._monitor()
        else:
            self._settrace()
        if sys.platform != "win32":
            os.register_at_fork(after_in_child=self._reset)
        import atexit

        atexit.register(self.finish)

    def _monitor(self):
        monitoring = sys.monitoring  # ty: ignore[missing-attribute]  # 3.12 and later
        events = monitoring.events
        disable = monitoring.DISABLE
        line = self.line
        for tool in (monitoring.DEBUGGER_ID, 3, 4, monitoring.PROFILER_ID):
            try:
                monitoring.use_tool_id(tool, "xpt probe")
                break
            except ValueError:
                continue
        else:
            self.error = "every sys.monitoring tool id is taken"
            return
        self.tracer = "monitoring"

        def start(code, offset):
            if self.wants(code):
                monitoring.set_local_events(tool, code, events.LINE)
            return disable

        def at_line(code, number):
            if number != line:
                return disable
            self.hit(sys._getframe(1))
            return None

        monitoring.register_callback(tool, events.PY_START, start)
        monitoring.register_callback(tool, events.LINE, at_line)
        monitoring.set_events(tool, events.PY_START)

    def _settrace(self):
        line = self.line

        def local(frame, event, arg):
            if event == "line" and frame.f_lineno == line:
                self.hit(frame)
            return local

        files = self.files
        wants = self.wants

        def calls(frame, event, arg):
            code = frame.f_code
            if files.get(code.co_filename) is False:
                return None
            return local if wants(code) else None

        sys.settrace(calls)
        self.tracer = "settrace"
        import threading

        threading.settrace(calls)


def install(text):
    config = json.loads(text)
    Probe(config).install(config.get("tracer", "auto"))
