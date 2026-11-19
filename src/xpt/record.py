from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TextIO, cast

from xpt.site import CallSite, Unlocatable
from xpt.value import Value, canonical, from_json, to_json

Status = Literal["missing", "mismatch", "unsupported"]

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 1_000_000
KEEP_RUNS = 5
DIRECTORY = ".xpt"

_STATUSES: tuple[Status, ...] = ("missing", "mismatch", "unsupported")
_RUN_ID = re.compile(r"\d{8}T\d{6}-[0-9a-f]{4}")


@dataclass(frozen=True, slots=True)
class Observation:
    run: str
    test: str
    site: CallSite | Unlocatable
    status: Status
    expected: Value | None
    actual: Value | None
    detail: str


class RecordError(ValueError):
    pass


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{secrets.token_hex(2)}"


@dataclass(frozen=True, slots=True)
class _Environment:
    record: bool
    worker: str


def _environment() -> _Environment:
    return _Environment(
        record=not os.environ.get("XPT_NO_RECORD"),
        worker=os.environ.get("PYTEST_XDIST_WORKER") or "main",
    )


@dataclass(slots=True)
class Recorder:
    """Appends this process's failed observations to `.xpt/runs/<run>/<worker>.jsonl`.

    `managed` means a driver, such as the pytest plugin, calls `finish()` at session end.
    An unmanaged recorder registers `finish()` with atexit on its first write.
    """

    root: Path
    run: str
    worker: str
    enabled: bool
    managed: bool = False
    test: str | None = None
    _file: TextIO | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls) -> Recorder:
        env = _environment()
        return cls(Path.cwd(), new_run_id(), env.worker, env.record)

    def configure(self, root: Path, run: str, worker: str, *, record: bool) -> None:
        self._close()
        self.root, self.run, self.worker = root, run, worker
        self.enabled = record and _environment().record
        self.managed = True
        self.test = None

    def record(self, observation: Observation) -> None:
        if not self.enabled:
            return
        line = _encode(observation)
        size = len(line.encode("utf-8"))
        if size > MAX_RECORD_BYTES:
            line = _encode(_without_values(observation, size))
        file = self._file or self._open()
        file.write(line + "\n")
        file.flush()

    def finish(self) -> None:
        self._close()
        directory = self.root / DIRECTORY
        if not self.enabled or not directory.is_dir():
            return
        temp = directory / f"latest.{self.run}.tmp"
        temp.write_text(self.run, encoding="utf-8")
        os.replace(temp, directory / "latest")
        runs = sorted(p for p in (directory / "runs").glob("*") if _RUN_ID.fullmatch(p.name))
        for old in runs[:-KEEP_RUNS]:
            shutil.rmtree(old, ignore_errors=True)

    def _open(self) -> TextIO:
        directory = self.root / DIRECTORY
        run_dir = directory / "runs" / self.run
        run_dir.mkdir(parents=True, exist_ok=True)
        gitignore = directory / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        self._file = (run_dir / f"{self.worker}.jsonl").open("a", encoding="utf-8", newline="\n")
        if not self.managed:
            self.managed = True
            atexit.register(self.finish)
        return self._file

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


recorder = Recorder.from_environment()


def load_run(root: Path, run: str | None = None) -> list[Observation]:
    """Read every observation of one run, the latest when `run` is None."""
    directory = root / DIRECTORY
    if run is None:
        latest = directory / "latest"
        if not latest.is_file():
            raise RecordError(f"{latest.as_posix()} does not exist. Run the tests to record a run.")
        run = _run_id(latest.read_text(encoding="utf-8").strip())
        if not (directory / "runs" / run).is_dir():
            return []
    run_dir = directory / "runs" / _run_id(run)
    if not run_dir.is_dir():
        raise RecordError(f"there is no run {run} in {(directory / 'runs').as_posix()}.")
    return [o for path in sorted(run_dir.glob("*.jsonl")) for o in _read(root, path)]


def _run_id(text: str) -> str:
    if not _RUN_ID.fullmatch(text):
        raise RecordError(f"{text!r} is not a run id. Run ids look like 20260101T120000-ab12.")
    return text


def _read(root: Path, path: Path) -> list[Observation]:
    shown = path.relative_to(root).as_posix()
    observations = []
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, 1):
            try:
                observations.append(_decode(json.loads(line)))
            except ValueError as error:
                raise RecordError(f"{shown}:{number}: {_reason(error)}") from error
    return observations


def _reason(error: ValueError) -> str:
    if isinstance(error, json.JSONDecodeError):
        return f"not JSON ({error.msg} at column {error.colno})"
    return str(error)


def _encode(o: Observation) -> str:
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run": o.run,
        "test": o.test,
        "site": _encode_site(o.site),
        "status": o.status,
        "detail": o.detail,
    }
    if o.expected is not None:
        record["expected"] = to_json(o.expected)
    if o.actual is not None:
        record["actual"] = to_json(o.actual)
    return json.dumps(record, ensure_ascii=False, allow_nan=False)


def _encode_site(site: CallSite | Unlocatable) -> dict[str, object]:
    if isinstance(site, Unlocatable):
        return {"kind": "unlocatable", "reason": site.reason, "path": site.path, "line": site.line}
    return {
        "kind": "call",
        "path": site.path,
        "qualname": site.qualname,
        "callee": site.callee,
        "ordinal": site.ordinal,
        "arg_sha": site.arg_sha,
        "line": site.line,
    }


def _without_values(o: Observation, size: int) -> Observation:
    value = o.actual if o.actual is not None else o.expected
    text = canonical(value) if value is not None else ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    detail = f"too large: {size} bytes, sha256 {digest}"
    return Observation(o.run, o.test, o.site, o.status, None, None, detail)


def _decode(data: object) -> Observation:
    record = _object(data, "the record")
    version = record.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"schema_version is {version!r}, and this xpt reads version 1.")
    status = _str(record, "status")
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {', '.join(_STATUSES)}, got {status!r}.")
    return Observation(
        run=_str(record, "run"),
        test=_str(record, "test"),
        site=_decode_site(_object(record.get("site"), "site")),
        status=status,
        expected=_value(record, "expected"),
        actual=_value(record, "actual"),
        detail=_str(record, "detail"),
    )


def _decode_site(site: dict[str, object]) -> CallSite | Unlocatable:
    kind = site.get("kind")
    if kind == "unlocatable":
        return Unlocatable(_str(site, "reason"), _str(site, "path"), _int(site, "line"))
    if kind == "call":
        return CallSite(
            _str(site, "path"),
            _str(site, "qualname"),
            _str(site, "callee"),
            _int(site, "ordinal"),
            _str(site, "arg_sha"),
            _int(site, "line"),
        )
    raise ValueError(f'site.kind must be "call" or "unlocatable", got {kind!r}.')


def _object(data: object, name: str) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object, got {data!r}.")
    return cast(dict[str, object], data)


def _str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string, got {value!r}.")
    return value


def _int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer, got {value!r}.")
    return value


def _value(data: dict[str, object], key: str) -> Value | None:
    if key not in data:
        return None
    try:
        return from_json(data[key])
    except ValueError as error:
        raise ValueError(f"{key}: {error}") from error
