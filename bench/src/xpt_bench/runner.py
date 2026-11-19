import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from xpt_bench import workspace
from xpt_bench.agents import command, parse_output
from xpt_bench.catalog import AgentSpec, Task, Variant
from xpt_bench.evaluate import evaluate
from xpt_bench.runs import RUNS_FILE, Run, run_id


@dataclass(frozen=True)
class Job:
    task: Task
    variant: Variant
    agent: AgentSpec
    rep: int

    @property
    def id(self) -> str:
        return run_id(self.task.name, self.variant.name, self.agent.label, self.rep)


@dataclass(frozen=True)
class Sweep:
    root: Path
    wheel: Path
    cache: Path
    out: Path
    words: list[str]
    timeout: int
    keep: bool


def plan(
    tasks: list[Task], variants: list[Variant], agents: list[AgentSpec], reps: int
) -> tuple[list[Job], list[str]]:
    jobs, skipped = [], []
    for task in tasks:
        for variant in variants:
            if not task.project(variant.fixture).is_dir():
                skipped.append(f"{task.name} has no {variant.fixture}/ for variant {variant.name}")
                continue
            for agent in agents:
                jobs += [Job(task, variant, agent, rep) for rep in range(1, reps + 1)]
    return jobs, skipped


def blinding_problems(jobs: list[Job], words: list[str]) -> list[str]:
    return [
        f"{task.name} prompt mentions {', '.join(found)}"
        for task in {job.task.name: job.task for job in jobs}.values()
        if (found := workspace.neutrality_problems(task.prompt, words))
    ]


def run_agent(
    cmd: list[str], cwd: Path, env: dict[str, str], logs: Path, timeout: int
) -> tuple[int | None, bool, float]:
    started = time.monotonic()
    with (logs / "agent.jsonl").open("w") as out, (logs / "agent.err").open("w") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return None, True, time.monotonic() - started
    return code, False, time.monotonic() - started


def execute(job: Job, sweep: Sweep) -> Run:
    logs = sweep.out / "runs" / job.id
    logs.mkdir(parents=True, exist_ok=True)
    base = Run(job.id, job.task.name, job.variant.name, job.agent.label, job.rep)
    env = workspace.clean_env(sweep.cache, job.variant.env)
    project = None
    try:
        project = workspace.prepare(
            job.task, job.variant, sweep.wheel, sweep.root, env, sweep.words
        )
        cmd = command(job.agent, job.task.prompt, project, sweep.cache)
        code, timed_out, wall = run_agent(cmd, project, env, logs, sweep.timeout)
        with (logs / "agent.jsonl").open() as lines:
            usage = parse_output(job.agent.kind, lines)
        patch = workspace.diff(project)
        (logs / "diff.patch").write_text(patch)
        changed = workspace.changed_files(project)
        seed = job.task.project(job.variant.fixture)
        outcome = evaluate(job.task, seed, project, env, logs, changed)
        return replace(
            base,
            usage=usage,
            outcome=outcome,
            wall_s=round(wall, 1),
            agent_exit=code,
            timed_out=timed_out,
        )
    except Exception as e:
        return replace(base, error=f"{type(e).__name__}: {e}")
    finally:
        if project is not None and not sweep.keep:
            shutil.rmtree(project.parent, ignore_errors=True)


def tool_version(name: str) -> str:
    try:
        done = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return done.stdout.strip() or done.stderr.strip()


def write_meta(out: Path, args: dict, wheel: workspace.Wheel, agents: list[AgentSpec]) -> None:
    kinds = sorted({a.kind for a in agents})
    meta = {
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "args": args,
        "xpt": {"source": wheel.source, "commit": wheel.commit, "sha256": wheel.sha256},
        "wheel": wheel.path.name,
        "agents": {kind: tool_version(kind) for kind in kinds},
        "uv": tool_version("uv"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def sweep(jobs: list[Job], sweep: Sweep, concurrency: int) -> list[Run]:
    lock = threading.Lock()
    runs = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(execute, job, sweep) for job in jobs]
        for done, future in enumerate(as_completed(futures), start=1):
            run = future.result()
            with lock, (sweep.out / RUNS_FILE).open("a") as f:
                f.write(run.to_json() + "\n")
            runs.append(run)
            status = run.error or (
                f"oracle {'pass' if run.outcome.oracle_passed else 'FAIL'}, "
                f"{run.usage.total_tokens} tokens, {run.wall_s}s"
            )
            print(f"[{done}/{len(jobs)}] {run.id}: {status}", flush=True)
    return runs
