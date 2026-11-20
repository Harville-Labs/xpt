import difflib
import json
import os
import random
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from xpt_bench.catalog import Task
from xpt_bench.report import JUDGE_FILE
from xpt_bench.runs import Run
from xpt_bench.workspace import codex_home

KEY_FILE = "judge-key.json"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["patches"],
    "properties": {
        "patches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "scores", "notes"],
                "properties": {
                    "label": {"type": "string"},
                    "notes": {"type": "string"},
                    "scores": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["criterion", "score"],
                            "properties": {
                                "criterion": {"type": "string"},
                                "score": {"type": "integer"},
                            },
                        },
                    },
                },
            },
        }
    },
}


class JudgeError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    label: str
    run: Run
    diff: str


@dataclass(frozen=True)
class Score:
    label: str
    scores: dict[str, int]
    notes: str

    @property
    def total(self) -> int:
        return sum(self.scores.values())


def blind(runs: list[Run], diffs: dict[str, str], seed: int) -> list[Entry]:
    order = sorted(runs, key=lambda r: r.id)
    random.Random(seed).shuffle(order)
    return [Entry(f"patch-{i:02d}", run, diffs[run.id]) for i, run in enumerate(order, start=1)]


def _files(root: Path) -> dict[str, list[str]]:
    return {
        p.relative_to(root).as_posix(): p.read_text().splitlines(keepends=True)
        for p in sorted(root.rglob("*.py"))
    }


def reference_diff(task: Task) -> str:
    before = _files(task.project("project"))
    after = _files(task.solution)
    return "".join(
        "".join(difflib.unified_diff(before.get(path, []), lines, f"a/{path}", f"b/{path}"))
        for path, lines in after.items()
    )


def build_prompt(rubric: str, reference: str, entries: list[Entry]) -> str:
    parts = [
        "You are grading patches against the rubric below. Grade each patch on its own merits.",
        "Return one entry per patch label, with a score for every rubric criterion.",
        "",
        rubric.strip(),
        "",
        "## Reference fix",
        "",
        "```diff",
        reference.rstrip(),
        "```",
    ]
    for entry in entries:
        body = entry.diff.rstrip() or "(empty patch: no files changed)"
        parts += ["", f"## {entry.label}", "", "```diff", body, "```"]
    return "\n".join(parts) + "\n"


def parse_scores(text: str, labels: list[str]) -> list[Score]:
    try:
        data = json.loads(text)
        scores = [
            Score(
                label=item["label"],
                scores={s["criterion"]: int(s["score"]) for s in item["scores"]},
                notes=item.get("notes", ""),
            )
            for item in data["patches"]
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise JudgeError(f"The judge's reply is not the expected JSON: {e}") from e
    missing = sorted(set(labels) - {s.label for s in scores})
    if missing:
        raise JudgeError(f"The judge skipped {', '.join(missing)}.")
    if any(not 0 <= v <= 2 for s in scores for v in s.scores.values()):
        raise JudgeError("The judge gave a score outside 0..2.")
    return [s for s in scores if s.label in labels]


def ask_codex(prompt: str, model: str | None, logs: Path, codex: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema.json"
        schema.write_text(json.dumps(SCHEMA))
        reply = Path(tmp) / "reply.json"
        workdir = Path(tmp) / "work"
        workdir.mkdir()
        cmd = ["codex", "exec", "--json", "-C", str(workdir), "--sandbox", "read-only"]
        cmd += ["--skip-git-repo-check", "--ignore-user-config", "--output-schema", str(schema)]
        cmd += ["-o", str(reply), *(["-m", model] if model else []), "-"]
        env = {**os.environ, "CODEX_HOME": str(codex_home(Path(tmp), codex))}
        done = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, check=False, env=env
        )
        logs.write_text(done.stdout + done.stderr)
        if done.returncode != 0 or not reply.is_file():
            raise JudgeError(f"codex exited {done.returncode}. Its output is in {logs}.")
        return reply.read_text()


def judge(
    results: Path,
    runs: list[Run],
    tasks: dict[str, Task],
    rubric: str,
    model: str | None,
    seed: int,
    codex: Path,
) -> list[dict]:
    rows, key = [], {}
    logs = results / "judge"
    logs.mkdir(exist_ok=True)
    for name in sorted({r.task for r in runs if r.error is None}):
        task_runs = [r for r in runs if r.task == name and r.error is None]
        diffs = {r.id: (results / "runs" / r.id / "diff.patch").read_text() for r in task_runs}
        entries = blind(task_runs, diffs, seed)
        prompt = build_prompt(rubric, reference_diff(tasks[name]), entries)
        (logs / f"{name}.prompt.md").write_text(prompt)
        reply = ask_codex(prompt, model, logs / f"{name}.jsonl", codex)
        by_label = {s.label: s for s in parse_scores(reply, [e.label for e in entries])}
        for entry in entries:
            score = by_label[entry.label]
            key[f"{name}/{entry.label}"] = entry.run.id
            rows.append(
                {
                    "run_id": entry.run.id,
                    "task": name,
                    "variant": entry.run.variant,
                    "agent": entry.run.agent,
                    "label": entry.label,
                    "scores": score.scores,
                    "total": score.total,
                    "notes": score.notes,
                    "judge_model": model or "codex default",
                }
            )
    (results / KEY_FILE).write_text(json.dumps(key, indent=2) + "\n")
    with (results / JUDGE_FILE).open("w") as f:
        f.writelines(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    return rows
