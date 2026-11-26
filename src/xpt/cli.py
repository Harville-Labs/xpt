from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from xpt import apiview, convert, decisions, env, flaky, init, probe, render, trace, traceback
from xpt.accept import AcceptError, AcceptResult, accept, lock
from xpt.changed import ChangedCode, Diff, changed_code
from xpt.changes import GitError, changes
from xpt.group import Group, group, missing, pick
from xpt.record import (
    DIRECTORY,
    SCHEMA_VERSION,
    RecordError,
    latest_run,
    load_run,
    run_ids,
)
from xpt.site import CallSite, SiteKey, short_ids
from xpt.value import Json
from xpt.verify import accepted_tests, rerun


class UsageError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Output:
    """A command's result in both forms, and its exit code."""

    code: int
    lines: list[str]
    data: dict[str, Json]


Command = Callable[[argparse.Namespace, Path], Output]


@dataclass(frozen=True, slots=True)
class _Run:
    id: str
    groups: tuple[Group, ...]
    run_option: str


def _load(root: Path, run: str | None) -> _Run:
    groups = decisions.mark(group(load_run(root, run)), decisions.load_rejections(root))
    id = run or latest_run(root)
    groups = flaky.mark(groups, flaky.find(flaky.load_history(root), id))
    return _Run(id, groups, f" --run {run}" if run else "")


def _pick(run: _Run, ids: Sequence[str]) -> list[Group]:
    chosen, unknown = pick(run.groups, ids)
    if unknown:
        raise UsageError(
            f"no group {', '.join(unknown)} in run {run.id}. Run xpt failures to list the ids."
        )
    return chosen


def failures(args: argparse.Namespace, root: Path) -> Output:
    run = _load(root, args.run)
    return Output(
        1 if run.groups else 0,
        render.failures_lines(run.id, run.groups, run.run_option),
        {"run": run.id, "groups": [render.group_json(g) for g in run.groups]},
    )


def _changed(
    root: Path, run: _Run, groups: Sequence[Group], since: str | None
) -> dict[str, ChangedCode]:
    """The changed code each group's tests ran, by group id. git runs only when the run
    has a trace."""
    found = trace.load(root, run.id)
    diff: Diff = GitError("no trace")
    if found is not None:
        try:
            diff = changes(root, since)
        except GitError as error:
            diff = error
    return {g.id: changed_code(root, found, g.tests, diff) for g in groups if g.id is not None}


def show(args: argparse.Namespace, root: Path) -> Output:
    run = _load(root, args.run)
    [g] = _pick(run, [args.id])
    changed = _changed(root, run, [g], args.since).get(g.id or "")
    data = render.group_json(g)
    if changed is not None:
        data["changed_code"] = render.changed_json(changed)
    return Output(
        0,
        render.show_lines(g, args.full, run.run_option, changed),
        {"run": run.id, "group": data},
    )


def accept_command(args: argparse.Namespace, root: Path) -> Output:
    chosen = [bool(args.ids), args.all, args.missing].count(True)
    if chosen == 0:
        raise UsageError(
            "xpt accept needs ids, --missing, or --all. Nothing was accepted. "
            "Run xpt failures to list the ids."
        )
    if chosen > 1:
        raise UsageError("give ids, --missing, or --all, not more than one.")
    if args.verify and args.dry_run:
        raise UsageError(
            "--verify reruns the tests after writing, so it can't be used with --dry-run."
        )
    run = _load(root, args.run)
    if args.all:
        groups, target = [g for g in run.groups if not g.rejected], "--all"
    elif args.missing:
        groups, target = missing(run.groups), "--missing"
    else:
        groups, target = _pick(run, args.ids), " ".join(args.ids)
    if args.force:
        target += " --force"
    data: dict[str, Json] = {"run": run.id, "dry_run": args.dry_run}
    if not groups:
        empty = render.accept_json(AcceptResult((), ()))
        return Output(0, [f"Nothing to accept in run {run.id}."], data | empty)
    changed = _changed(root, run, groups, args.since)
    result = accept(root, groups, dry_run=args.dry_run, force=args.force)
    written = all(o.status in ("accepted", "already_accepted") for o in result.outcomes)
    lines = render.accept_lines(
        result, args.dry_run, run.run_option, target, verified=args.verify, changed=changed
    )
    data |= render.accept_json(result, changed)
    if not args.verify:
        return Output(0 if written else 1, lines, data)
    checked = rerun(root, accepted_tests(result.outcomes))
    code = 0 if written and checked.passed is not False else 1
    return Output(
        code, lines + render.rerun_lines(checked), data | {"verify": render.rerun_json(checked)}
    )


def reject(args: argparse.Namespace, root: Path) -> Output:
    run = _load(root, args.run)
    outcomes = decisions.reject(root, run.id, _pick(run, args.ids))
    return Output(
        1 if any(o.status == "skipped" for o in outcomes) else 0,
        render.reject_lines(outcomes),
        {"run": run.id, "outcomes": [render.outcome_json(o) for o in outcomes]},
    )


def runs(args: argparse.Namespace, root: Path) -> Output:
    recorded = run_ids(root)
    latest = latest_run(root) if (root / DIRECTORY / "latest").is_file() else None
    every = sorted({*recorded, *([latest] if latest else [])}, reverse=True)
    rows = [(r, r == latest, len(group(load_run(root, r))) if r in recorded else 0) for r in every]
    return Output(
        0,
        render.runs_lines(rows),
        {"runs": [{"id": r, "latest": latest, "failures": n} for r, latest, n in rows]},
    )


def flaky_command(args: argparse.Namespace, root: Path) -> Output:
    history = flaky.load_history(root)
    found: dict[SiteKey, tuple[CallSite, tuple[flaky.Volatile, ...]]] = {}
    for key, seen in history.items():
        last = seen[-1]
        if paths := flaky.volatile(history, key, last.run):
            found[key] = (last.site, paths)
    ids = short_ids(s.site for seen in history.values() for s in seen)
    sites = sorted(
        ((ids[key], site, paths) for key, (site, paths) in found.items()),
        key=lambda s: (s[1].path, s[1].line, s[1].ordinal),
    )
    runs = run_ids(root)
    return Output(
        1 if sites else 0,
        render.flaky_sites_lines(sites, len(runs)),
        {
            "runs": list(runs),
            "sites": [render.flaky_site_json(*s) for s in sites],
        },
    )


def clean(args: argparse.Namespace, root: Path) -> Output:
    directory = root / DIRECTORY
    if not directory.is_dir():
        return Output(0, ["Nothing to clean, .xpt/ does not exist."], {"removed": False})
    with lock(directory / "accept.lock"):
        shutil.rmtree(directory)
    return Output(0, ["Removed .xpt/."], {"removed": True})


def init_command(args: argparse.Namespace, root: Path) -> Output:
    result = init.init(root, init.probe(), os.environ, dry_run=args.dry_run, mcp=args.mcp)
    return Output(
        0 if all(c.ok for c in result.checks) else 1,
        init.lines(result, args.dry_run),
        init.to_json(result, args.dry_run),
    )


def convert_command(args: argparse.Namespace, root: Path) -> Output:
    result = convert.convert(root, args.paths, dry_run=args.dry_run)
    retry = " ".join(["xpt convert", *(p.as_posix() for p in args.paths)])
    return Output(
        0,
        convert.lines(result, args.dry_run, retry),
        convert.to_json(result, args.dry_run),
    )


def env_command(args: argparse.Namespace, root: Path) -> Output:
    return Output(*env.command(root, args.name, os.environ))


def api_command(args: argparse.Namespace, root: Path) -> Output:
    result = apiview.listing(
        args.target, args.depth, args.grep, args.limit, use_import=args.use_import
    )
    return Output(0, apiview.lines(result), apiview.to_json(result))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{message}. Run {self.prog} --help for usage.")


def _json_only() -> _Parser:
    parent = _Parser(add_help=False)
    parent.add_argument("--json", action="store_true", help="print one JSON object")
    return parent


def _parser() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--root", type=Path, default=Path(), help="the project root (default: .)")
    picked = _Parser(add_help=False)
    picked.add_argument("--run", help="a run id from xpt runs (default: the latest run)")
    since = _Parser(add_help=False)
    since.add_argument(
        "--since",
        metavar="REF",
        help="the git ref to diff the changed code against (default: the merge base with "
        "the default branch, or HEAD)",
    )

    parser = _Parser(
        prog="xpt",
        description="Review, accept, and reject recorded expect() failures, set a project up "
        "for xpt, and convert assert-equals tests.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Command, help: str, *parents: _Parser) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help, parents=[common, *parents])
        sub.set_defaults(handler=handler)
        return sub

    add("failures", failures, "list the failed call sites of a run, with their diffs", picked)
    sub = add("show", show, "show one call site", picked, since)
    sub.add_argument("id")
    sub.add_argument("--full", action="store_true", help="lift the change and length limits")
    sub = add("accept", accept_command, "write actual values into the test source", picked, since)
    sub.add_argument("ids", nargs="*", metavar="id")
    sub.add_argument("--all", action="store_true", help="every acceptable call site")
    sub.add_argument("--missing", action="store_true", help="only empty expectations")
    sub.add_argument("--dry-run", action="store_true", help="report and write nothing")
    sub.add_argument(
        "--force", action="store_true", help="accept values that look broken, such as exceptions"
    )
    sub.add_argument(
        "--verify", action="store_true", help="rerun the tests that observed the accepted sites"
    )
    sub = add("reject", reject, "record actual values as bugs, never to be offered", picked)
    sub.add_argument("ids", nargs="+", metavar="id")
    add("runs", runs, "list recorded runs")
    add("flaky", flaky_command, "list call sites whose values change between stored runs")
    add("clean", clean, "delete .xpt/ and every recorded value")
    sub = add("init", init_command, "add xpt's agent instructions to AGENTS.md and check pytest")
    sub.add_argument("--dry-run", action="store_true", help="report and write nothing")
    sub.add_argument("--mcp", action="store_true", help="also add xpt mcp to .mcp.json")
    sub = add("convert", convert_command, "rewrite assert x == literal as expect(x, literal)")
    sub.add_argument("paths", nargs="+", type=Path, metavar="path")
    sub.add_argument("--dry-run", action="store_true", help="report and write nothing")
    sub = add("env", env_command, "report the interpreter, shadowed modules, and lockfile drift")
    sub.add_argument("name", nargs="?", help="a module to locate without importing it")
    sub = commands.add_parser(
        "api",
        help="list compact signatures of an installed package, module, class, or function",
        parents=[_json_only()],
    )
    sub.set_defaults(handler=api_command, root=Path())
    sub.add_argument("target", help="a dotted name, as in json, json.dumps, or pathlib.Path")
    sub.add_argument("--depth", type=int, default=0, help="also list submodules this deep")
    sub.add_argument("--grep", metavar="WORD", help="only names that contain WORD")
    sub.add_argument(
        "--limit", type=int, default=apiview.LIMIT, help="most names to list, 0 for all"
    )
    sub.add_argument(
        "--import",
        dest="use_import",
        action="store_true",
        help="import the module to read what its source and stubs can't show; runs its code",
    )
    sub = commands.add_parser(
        "run",
        help="run a command whose Python processes print compact tracebacks",
        usage="xpt run [-v] -- command [arg ...]",
    )
    sub.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="raise the limits; -vv prints Python's full traceback",
    )
    sub.add_argument("argv", nargs=argparse.REMAINDER, metavar="command")
    sub = commands.add_parser("mcp", help="serve the review commands as MCP tools over stdio")
    sub.add_argument("--root", type=Path, default=Path(), help="the project root (default: .)")
    commands.add_parser(
        "probe", help="record the values at one line while a command runs", usage=probe.USAGE
    )
    return parser


def _run(argv: Sequence[str], verbose: int) -> int:
    command = list(argv[1:] if argv[:1] == ["--"] else argv)
    if not command:
        raise UsageError("xpt run needs a command, as in xpt run -- python app.py.")
    return traceback.run(command, verbose)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["probe"]:
        return probe.main(argv[1:])
    try:
        args = _parser().parse_args(argv)
        if args.command == "run":
            return _run(args.argv, args.verbose)
        if args.command == "mcp":
            from xpt import mcp

            return mcp.main(args.root)
        output: Output = args.handler(args, args.root)
    except (
        UsageError,
        RecordError,
        AcceptError,
        convert.ConvertError,
        init.InitError,
        apiview.ApiError,
    ) as error:
        print(f"xpt: error: {error}", file=sys.stderr)
        return 2
    if args.json:
        payload = {"schema_version": SCHEMA_VERSION, "command": args.command, **output.data}
        print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    else:
        print("\n".join(output.lines))
    return output.code
