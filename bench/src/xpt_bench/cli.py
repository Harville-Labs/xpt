import argparse
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from xpt_bench import report, workspace
from xpt_bench.catalog import (
    BENCH_ROOT,
    RUBRIC_FILE,
    CatalogError,
    load_tasks,
    load_variants,
    parse_agent,
)
from xpt_bench.judge import JudgeError, judge
from xpt_bench.runner import Sweep, blinding_problems, plan, sweep, write_meta
from xpt_bench.runs import load_runs
from xpt_bench.taskcheck import check_task

RESULTS_DIR = BENCH_ROOT / "results"


def codex_source() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _run(args: argparse.Namespace) -> int:
    agents = [parse_agent(a) for a in args.agents.split(",")]
    tasks = load_tasks(args.tasks)
    variants = load_variants(args.variants)
    words = workspace.banned_words(
        [v.name for v in variants] + [a.model for a in agents if a.model]
    )
    jobs, skipped = plan(tasks, variants, agents, args.reps)
    for line in skipped:
        print(f"skip: {line}")
    problems = blinding_problems(jobs, words)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    out = Path(args.out or RESULTS_DIR / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")).resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = workspace.sweep_root(args.workroot, words)
    try:
        wheel = workspace.build_wheel(args.xpt, root / "dist")
        write_meta(
            out,
            {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items() if k != "func"},
            wheel,
            agents,
        )
        settings = Sweep(
            root=root,
            wheel=wheel.path,
            cache=root / "cache",
            out=out,
            words=words,
            timeout=args.timeout,
            keep=args.keep_workdirs,
            codex_home=workspace.codex_home(root, codex_source())
            if any(a.kind == "codex" for a in agents)
            else None,
        )
        print(f"{len(jobs)} runs, {args.jobs} at a time. Results: {out}")
        runs = sweep(jobs, settings, args.jobs)
    finally:
        if not args.keep_workdirs:
            shutil.rmtree(root, ignore_errors=True)
    groups = report.summarize(runs)
    print()
    print(report.render(groups, report.compare(runs, "stock")))
    return 0


def _report(args: argparse.Namespace) -> int:
    runs, judge_scores = [], {}
    for path in args.results:
        runs += load_runs(path)
        judge_scores |= report.load_judge(path)
    groups = report.summarize(runs, judge_scores)
    verdicts = report.compare(runs, args.baseline, seed=args.seed)
    show = report.render_json if args.json else report.render
    print(show(groups, verdicts))
    return 0


def _judge(args: argparse.Namespace) -> int:
    runs = load_runs(args.results)
    tasks = {t.name: t for t in load_tasks("all")}
    rubric = RUBRIC_FILE.read_text()
    rows = judge(args.results, runs, tasks, rubric, args.model, args.seed, codex_source())
    print(f"Scored {len(rows)} runs. Scores: {args.results / report.JUDGE_FILE}")
    return 0


def _check(args: argparse.Namespace) -> int:
    words = workspace.banned_words([])
    root = workspace.sweep_root(args.workroot, words)
    logs = Path(args.logs) if args.logs else root / "logs"
    failed = False
    try:
        wheel = workspace.build_wheel(args.xpt, root / "dist")
        for task in load_tasks(args.tasks):
            result = check_task(task, wheel.path, root, root / "cache", logs)
            failed |= bool(result.problems)
            status = "ok" if not result.problems else "FAILED"
            print(f"{task.name}: {status}")
            for problem in result.problems:
                print(f"  {problem}")
            trips = ", ".join(result.accept_all_trips) or "none"
            print(f"  blind `xpt accept --all` trips: {trips}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xpt-bench")
    sub = p.add_subparsers(required=True)

    run = sub.add_parser("run", help="run agents on tasks and record one row per run")
    run.add_argument("--agents", required=True, help="e.g. claude:sonnet,codex:default")
    run.add_argument("--variants", default="all", help="names from variants.toml, or all")
    run.add_argument("--tasks", default="all", help="task names, or all")
    run.add_argument("--reps", type=int, default=5)
    run.add_argument("--xpt", default="HEAD", help="a git ref of this repo, a directory, or a .whl")
    run.add_argument("--jobs", type=int, default=4, help="runs at a time")
    run.add_argument("--timeout", type=int, default=1800, help="seconds per agent run")
    run.add_argument("--out", help="results directory (default bench/results/<UTC time>)")
    run.add_argument(
        "--workroot", type=Path, help="where run directories go (default: system temp)"
    )
    run.add_argument("--keep-workdirs", action="store_true")
    run.set_defaults(func=_run)

    rep = sub.add_parser("report", help="summarize one or more results directories")
    rep.add_argument("results", nargs="+", type=Path)
    rep.add_argument("--baseline", default="stock")
    rep.add_argument("--seed", type=int, default=0, help="bootstrap seed")
    rep.add_argument("--json", action="store_true", help="print JSON with every interval")
    rep.set_defaults(func=_report)

    jdg = sub.add_parser("judge", help="score diffs blind with codex and the rubric")
    jdg.add_argument("results", type=Path)
    jdg.add_argument("--model", help="codex model (default: codex's default)")
    jdg.add_argument("--seed", type=int, default=0, help="shuffle seed for the labels")
    jdg.set_defaults(func=_judge)

    chk = sub.add_parser("check", help="prove each seed fails and each solution passes")
    chk.add_argument("--tasks", default="all")
    chk.add_argument("--xpt", default="HEAD")
    chk.add_argument("--workroot", type=Path)
    chk.add_argument("--logs", help="keep the check logs here")
    chk.set_defaults(func=_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (CatalogError, workspace.WorkspaceError, JudgeError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
