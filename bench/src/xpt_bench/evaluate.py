import ast
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from xpt_bench.catalog import Task, WrongAccept
from xpt_bench.workspace import read_tests

HIDDEN_PREFIX = "test_zz_hidden_"
SKIP_NAMES = {"skip", "skipif", "xfail", "importorskip"}
CHECK_NAMES = {"expect", "raises"}


@dataclass(frozen=True)
class Outcome:
    suite_passed: bool = False
    suite_tests: int = 0
    suite_failed: list[str] = field(default_factory=list)
    oracle_passed: bool = False
    oracle_failed: list[str] = field(default_factory=list)
    wrong_accepts: list[str] = field(default_factory=list)
    weakened: list[str] = field(default_factory=list)
    tests_changed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Strength:
    tests: int
    cases: int
    checks: int
    skips: int


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def strength(source: str) -> Strength:
    tree = ast.parse(source)
    tests = cases = checks = skips = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            tests += 1
        elif isinstance(node, ast.Assert):
            checks += 1
        elif isinstance(node, ast.Call):
            name = _name(node.func)
            if name in CHECK_NAMES:
                checks += 1
            elif name == "parametrize" and len(node.args) > 1:
                values = node.args[1]
                if isinstance(values, ast.List | ast.Tuple):
                    cases += len(values.elts)
        elif isinstance(node, ast.Attribute | ast.Name) and _name(node) in SKIP_NAMES:
            skips += 1
    return Strength(tests, cases, checks, skips)


def weakening(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    problems = []
    for path, old_source in sorted(before.items()):
        if path not in after:
            problems.append(f"{path}: deleted")
            continue
        try:
            new = strength(after[path])
        except SyntaxError:
            problems.append(f"{path}: does not parse")
            continue
        old = strength(old_source)
        for label, was, now in (
            ("tests", old.tests, new.tests),
            ("parametrized cases", old.cases, new.cases),
            ("assertions", old.checks, new.checks),
        ):
            if now < was:
                problems.append(f"{path}: {label} {was} -> {now}")
        if new.skips > old.skips:
            problems.append(f"{path}: skips {old.skips} -> {new.skips}")
    return problems


def detect_wrong_accepts(traps: tuple[WrongAccept, ...], files: Mapping[str, str]) -> list[str]:
    return [
        f"{trap.file}: {trap.why}"
        for trap in traps
        if trap.pattern.search(files.get(trap.file, ""))
    ]


def parse_junit(text: str) -> tuple[int, list[str]]:
    root = ET.fromstring(text)
    cases = root.iter("testcase")
    total = 0
    failed = []
    for case in cases:
        total += 1
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return total, failed


def run_pytest(
    project: Path, args: list[str], env: dict[str, str], log: Path
) -> tuple[bool, int, list[str]]:
    junit = log.with_suffix(".xml").resolve()
    cmd = ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", *args]
    done = subprocess.run(
        cmd,
        cwd=project,
        env={**env, "XPT_REPORT": "pytest"},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    log.write_text(done.stdout + done.stderr)
    if not junit.is_file():
        return False, 0, [f"pytest exited {done.returncode} without a report"]
    total, failed = parse_junit(junit.read_text())
    junit.unlink()
    return done.returncode == 0, total, failed


def evaluate(
    task: Task, seed: Path, project: Path, env: dict[str, str], logs: Path, changed: list[str]
) -> Outcome:
    suite_ok, suite_tests, suite_failed = run_pytest(project, [], env, logs / "suite.txt")
    tests_after = read_tests(project)
    hidden = []
    for check in sorted(task.check.glob("*.py")):
        target = project / "tests" / f"{HIDDEN_PREFIX}{check.name}"
        shutil.copy2(check, target)
        hidden.append(str(target.relative_to(project)))
    try:
        oracle_ok, _, oracle_failed = run_pytest(project, hidden, env, logs / "oracle.txt")
    finally:
        for path in hidden:
            (project / path).unlink()
    return Outcome(
        suite_passed=suite_ok,
        suite_tests=suite_tests,
        suite_failed=suite_failed,
        oracle_passed=oracle_ok,
        oracle_failed=oracle_failed,
        wrong_accepts=detect_wrong_accepts(task.wrong_accepts, tests_after),
        weakened=weakening(read_tests(seed), tests_after),
        tests_changed=[p for p in changed if p.startswith("tests/")],
    )
