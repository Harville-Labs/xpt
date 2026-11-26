"""`xpt mcp`: the review commands as MCP tools, served over stdio with the standard library.

The transport is JSON-RPC 2.0, one message per line on stdin and stdout. Each tool calls
the same handler as its CLI command and returns the object that command's `--json` prints,
so the guards that `xpt accept` applies hold for a tool call too.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import sys
import traceback
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from xpt import cli, convert, init
from xpt.accept import AcceptError
from xpt.record import SCHEMA_VERSION, RecordError

# Newest first. A client that asks for one of these gets it back; any other gets the first.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_STRUCTURED_SINCE = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_ERRORS = (cli.UsageError, RecordError, AcceptError, convert.ConvertError, init.InitError)

INSTRUCTIONS = (
    "xpt records failed expect() calls when the tests run. xpt_failures lists them with "
    "diffs, xpt_show shows one, xpt_accept writes actual values into the test source, and "
    "xpt_reject marks a value as a bug. Read each diff before accepting. Accept refuses "
    "stale, suspect, and flaky values unless force is true; pass force only when the value "
    "is really the intended result."
)


@dataclass(frozen=True, slots=True)
class Param:
    type: str
    description: str
    min_items: int = 0


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    title: str
    description: str
    command: str
    handler: cli.Command
    params: dict[str, Param]
    required: tuple[str, ...] = ()
    read_only: bool = True

    def schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for name, p in self.params.items():
            if p.type == "array":
                prop: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
                if p.min_items:
                    prop["minItems"] = p.min_items
            else:
                prop = {"type": p.type}
            properties[name] = prop | {"description": p.description}
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if self.required:
            schema["required"] = list(self.required)
        return schema

    def describe(self) -> dict[str, Any]:
        hints = {"readOnlyHint": self.read_only, "openWorldHint": False}
        if not self.read_only:
            hints |= {"destructiveHint": False, "idempotentHint": True}
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema(),
            "annotations": {"title": self.title, **hints},
        }


_RUN = Param("string", "A run id from xpt_runs. Default: the latest run.")
_SINCE = Param(
    "string",
    "The git ref to diff the changed code against. Default: the merge base with the "
    "default branch, or HEAD.",
)
_IDS = "Call site ids from xpt_failures."

TOOLS = {
    t.name: t
    for t in (
        Tool(
            "xpt_failures",
            "List failures",
            "List the failed expect() call sites of a run, with their diffs. Same object as "
            "xpt failures --json.",
            "failures",
            cli.failures,
            {"run": _RUN},
        ),
        Tool(
            "xpt_show",
            "Show a call site",
            "Show one call site with its function and tests. Same object as xpt show --json.",
            "show",
            cli.show,
            {
                "id": Param("string", "A call site id from xpt_failures."),
                "run": _RUN,
                "full": Param("boolean", "Lift the change and length limits."),
                "since": _SINCE,
            },
            required=("id",),
        ),
        Tool(
            "xpt_accept",
            "Accept values",
            "Write actual values into the test source as the expected values. Give exactly one "
            "of ids, all, or missing. Stale, suspect, and flaky values are refused unless force "
            "is true. Same object as xpt accept --json.",
            "accept",
            cli.accept_command,
            {
                "ids": Param("array", _IDS),
                "all": Param("boolean", "Every acceptable call site that was not rejected."),
                "missing": Param("boolean", "Only empty expectations."),
                "dry_run": Param("boolean", "Report what would change and write nothing."),
                "force": Param("boolean", "Also accept values that look broken or flaky."),
                "verify": Param(
                    "boolean", "Rerun the tests that observed the accepted call sites."
                ),
                "run": _RUN,
                "since": _SINCE,
            },
            read_only=False,
        ),
        Tool(
            "xpt_reject",
            "Reject values",
            "Record actual values as bugs, so they are never offered for acceptance. Same "
            "object as xpt reject --json.",
            "reject",
            cli.reject,
            {"ids": Param("array", _IDS, min_items=1), "run": _RUN},
            required=("ids",),
            read_only=False,
        ),
        Tool(
            "xpt_runs",
            "List runs",
            "List recorded runs, newest first. Same object as xpt runs --json.",
            "runs",
            cli.runs,
            {},
        ),
        Tool(
            "xpt_flaky",
            "List flaky call sites",
            "List call sites whose values changed between stored runs with no source change. "
            "Same object as xpt flaky --json.",
            "flaky",
            cli.flaky_command,
            {},
        ),
    )
}

_DEFAULTS: dict[str, Any] = {"boolean": False, "string": None, "array": []}


class ProtocolError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _arguments(tool: Tool, given: object) -> argparse.Namespace:
    """The handler's namespace for `given`, or a ValueError that says what is wrong."""
    if given is None:
        given = {}
    if not isinstance(given, dict):
        raise ValueError("arguments must be an object.")
    unknown = sorted(set(given) - set(tool.params))
    if unknown:
        raise ValueError(f"{tool.name} has no argument {', '.join(unknown)}.")
    absent = [name for name in tool.required if name not in given]
    if absent:
        raise ValueError(f"{tool.name} needs {', '.join(absent)}.")
    values: dict[str, Any] = {}
    for name, p in tool.params.items():
        value = given.get(name, _DEFAULTS[p.type])
        if name in given and not _valid(p, value):
            kind = {"array": "an array of strings", "boolean": "a boolean", "string": "a string"}
            raise ValueError(f"{name} must be {kind[p.type]}.")
        if p.type == "array" and len(value) < p.min_items:
            raise ValueError(f"{name} needs at least {p.min_items} item.")
        values[name] = value
    return argparse.Namespace(**values)


def _valid(p: Param, value: object) -> bool:
    if p.type == "boolean":
        return isinstance(value, bool)
    if p.type == "string":
        return isinstance(value, str)
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _text(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


@dataclass
class Server:
    root: Path
    protocol: str = PROTOCOL_VERSIONS[0]

    def handle(self, line: bytes | str) -> dict[str, Any] | None:
        """The response to one message, or None for a notification."""
        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError) as error:
            return _error(None, PARSE_ERROR, f"Parse error: {error}.")
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "A message must be one JSON object.")
        id = message.get("id")
        if "id" in message and (isinstance(id, bool) or not isinstance(id, str | int)):
            return _error(None, INVALID_REQUEST, "id must be a string or an integer.")
        method = message.get("method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            if "method" not in message and ("result" in message or "error" in message):
                return None
            return _error(id, INVALID_REQUEST, 'A request needs "jsonrpc": "2.0" and a method.')
        params = message.get("params", {})
        if "id" not in message:
            return None
        try:
            if not isinstance(params, dict):
                raise ProtocolError(INVALID_PARAMS, "params must be an object.")
            return {"jsonrpc": "2.0", "id": id, "result": self._call(method, params)}
        except ProtocolError as error:
            return _error(id, error.code, str(error))
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            return _error(id, INTERNAL_ERROR, f"Internal error: {error!r}.")

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [t.describe() for t in TOOLS.values()]}
        if method == "tools/call":
            return self._tool(params)
        raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}.")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        self.protocol = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": self.protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "xpt", "title": "xpt", "version": _version()},
            "instructions": INSTRUCTIONS,
        }

    def _tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = TOOLS.get(name) if isinstance(name, str) else None
        if tool is None:
            raise ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}.")
        try:
            args = _arguments(tool, params.get("arguments"))
        except ValueError as error:
            return _text(f"xpt: error: {error}", error=True)
        try:
            output = tool.handler(args, self.root)
        except _ERRORS as error:
            return _text(f"xpt: error: {error}", error=True)
        payload = {"schema_version": SCHEMA_VERSION, "command": tool.command, **output.data}
        result = _text(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        if self.protocol >= _STRUCTURED_SINCE:
            result["structuredContent"] = payload
        return result


def _error(id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def _version() -> str:
    try:
        return importlib.metadata.version("xpt")
    except importlib.metadata.PackageNotFoundError:
        return "0"


def serve(root: Path, lines: Iterable[bytes], out: IO[bytes], log: IO[str]) -> int:
    """Answer each line until the input ends. Only protocol messages are written to `out`;
    anything else that prints during a tool call goes to `log`."""
    server = Server(root)
    for line in lines:
        if not line.strip():
            continue
        with contextlib.redirect_stdout(log):
            response = server.handle(line)
        if response is not None:
            _write(out, response)
    return 0


def _write(out: IO[bytes], response: dict[str, Any]) -> None:
    out.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    out.flush()


def main(root: Path) -> int:
    out = sys.stdout.buffer
    print(f"xpt mcp: serving {root.resolve().as_posix()} over stdio.", file=sys.stderr)
    try:
        return serve(root, sys.stdin.buffer, out, sys.stderr)
    except KeyboardInterrupt:
        return 0
