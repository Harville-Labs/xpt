import subprocess
from dataclasses import dataclass
from pathlib import Path

from xpt_bench import workspace
from xpt_bench.catalog import Task, Variant
from xpt_bench.evaluate import Outcome, detect_wrong_accepts, evaluate

CHECK_VARIANT = Variant(name="check", description="task check")


@dataclass(frozen=True)
class TaskReport:
    task: str
    problems: list[str]
    accept_all_trips: list[str]


def seed_problems(outcome: Outcome) -> list[str]:
    problems = []
    if outcome.suite_passed:
        problems.append("seed suite passes; it must fail")
    if outcome.oracle_passed:
        problems.append("seed passes the hidden check; it must fail")
    problems += [f"seed trips a wrong-accept trap: {w}" for w in outcome.wrong_accepts]
    problems += [f"seed looks weakened: {w}" for w in outcome.weakened]
    return problems


def solution_problems(outcome: Outcome) -> list[str]:
    problems = []
    if not outcome.suite_passed:
        problems.append(f"solution suite fails: {', '.join(outcome.suite_failed)}")
    if not outcome.oracle_passed:
        problems.append(f"solution fails the hidden check: {', '.join(outcome.oracle_failed)}")
    problems += [f"solution trips a wrong-accept trap: {w}" for w in outcome.wrong_accepts]
    problems += [f"solution looks weakened: {w}" for w in outcome.weakened]
    return problems


def check_task(task: Task, wheel: Path, root: Path, cache: Path, logs: Path) -> TaskReport:
    env = workspace.clean_env(cache)
    words = workspace.banned_words([])
    seed_dir = task.project(CHECK_VARIANT.fixture)
    problems = []
    for label, overlay, review in (
        ("seed", None, seed_problems),
        ("solution", task.solution, solution_problems),
    ):
        out = logs / task.name / label
        out.mkdir(parents=True, exist_ok=True)
        project = workspace.prepare(task, CHECK_VARIANT, wheel, root, env, words, overlay)
        changed = workspace.changed_files(project)
        problems += review(evaluate(task, seed_dir, project, env, out, changed))
    project = workspace.prepare(task, CHECK_VARIANT, wheel, root, env, words)
    subprocess.run(["uv", "run", "pytest", "-q"], cwd=project, env=env, capture_output=True)
    subprocess.run(
        ["uv", "run", "xpt", "accept", "--all"], cwd=project, env=env, capture_output=True
    )
    trips = detect_wrong_accepts(task.wrong_accepts, workspace.read_tests(project))
    return TaskReport(task.name, problems, trips)
