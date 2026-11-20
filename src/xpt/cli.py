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

from xpt import convert, decisions, init, render
from xpt.accept import AcceptError, AcceptResult, accept, lock
from xpt.group import Group, group, missing
from xpt.record import (
    DIRECTORY,
    SCHEMA_VERSION,
    RecordError,
    latest_run,
    load_run,
    run_ids,
)
from xpt.value import Json


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
    return _Run(run or latest_run(root), groups, f" --run {run}" if run else "")


def _pick(run: _Run, ids: Sequence[str]) -> list[Group]:
    by_id = {g.id: g for g in run.groups if g.id is not None}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise UsageError(
            f"no group {', '.join(unknown)} in run {run.id}. Run xpt failures to list the ids."
        )
    return [by_id[i] for i in dict.fromkeys(ids)]


def failures(args: argparse.Namespace, root: Path) -> Output:
    run = _load(root, args.run)
    return Output(
        1 if run.groups else 0,
        render.failures_lines(run.id, run.groups, run.run_option),
        {"run": run.id, "groups": [render.group_json(g) for g in run.groups]},
    )


def show(args: argparse.Namespace, root: Path) -> Output:
    run = _load(root, args.run)
    [g] = _pick(run, [args.id])
    return Output(
        0,
        render.show_lines(g, args.full, run.run_option),
        {"run": run.id, "group": render.group_json(g)},
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
    run = _load(root, args.run)
    if args.all:
        groups, target = [g for g in run.groups if not g.rejected], "--all"
    elif args.missing:
        groups, target = missing(run.groups), "--missing"
    else:
        groups, target = _pick(run, args.ids), " ".join(args.ids)
    data: dict[str, Json] = {"run": run.id, "dry_run": args.dry_run}
    if not groups:
        empty = render.accept_json(AcceptResult((), ()))
        return Output(0, [f"Nothing to accept in run {run.id}."], data | empty)
    result = accept(root, groups, dry_run=args.dry_run)
    written = all(o.status in ("accepted", "already_accepted") for o in result.outcomes)
    retry = f"xpt accept{run.run_option} {target}"
    return Output(
        0 if written else 1,
        render.accept_lines(result, args.dry_run, retry),
        data | render.accept_json(result),
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


def clean(args: argparse.Namespace, root: Path) -> Output:
    directory = root / DIRECTORY
    if not directory.is_dir():
        return Output(0, ["Nothing to clean, .xpt/ does not exist."], {"removed": False})
    with lock(directory / "accept.lock"):
        shutil.rmtree(directory)
    return Output(0, ["Removed .xpt/."], {"removed": True})


def init_command(args: argparse.Namespace, root: Path) -> Output:
    result = init.init(root, init.probe(), os.environ, dry_run=args.dry_run)
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


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{message}. Run {self.prog} --help for usage.")


def _parser() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--root", type=Path, default=Path(), help="the project root (default: .)")
    picked = _Parser(add_help=False)
    picked.add_argument("--run", help="a run id from xpt runs (default: the latest run)")

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
    sub = add("show", show, "show one call site", picked)
    sub.add_argument("id")
    sub.add_argument("--full", action="store_true", help="lift the change and length limits")
    sub = add("accept", accept_command, "write actual values into the test source", picked)
    sub.add_argument("ids", nargs="*", metavar="id")
    sub.add_argument("--all", action="store_true", help="every acceptable call site")
    sub.add_argument("--missing", action="store_true", help="only empty expectations")
    sub.add_argument("--dry-run", action="store_true", help="report and write nothing")
    sub = add("reject", reject, "record actual values as bugs, never to be offered", picked)
    sub.add_argument("ids", nargs="+", metavar="id")
    add("runs", runs, "list recorded runs")
    add("clean", clean, "delete .xpt/ and every recorded value")
    sub = add("init", init_command, "add xpt's agent instructions to AGENTS.md and check pytest")
    sub.add_argument("--dry-run", action="store_true", help="report and write nothing")
    sub = add("convert", convert_command, "rewrite assert x == literal as expect(x, literal)")
    sub.add_argument("paths", nargs="+", type=Path, metavar="path")
    sub.add_argument("--dry-run", action="store_true", help="report and write nothing")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        output: Output = args.handler(args, args.root)
    except (UsageError, RecordError, AcceptError, convert.ConvertError, init.InitError) as error:
        print(f"xpt: error: {error}", file=sys.stderr)
        return 2
    if args.json:
        payload = {"schema_version": SCHEMA_VERSION, "command": args.command, **output.data}
        print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    else:
        print("\n".join(output.lines))
    return output.code
