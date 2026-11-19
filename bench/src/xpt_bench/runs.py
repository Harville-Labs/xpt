import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from xpt_bench.agents import Usage
from xpt_bench.evaluate import Outcome

RUNS_FILE = "runs.jsonl"


@dataclass(frozen=True)
class Run:
    id: str
    task: str
    variant: str
    agent: str
    rep: int
    wall_s: float = 0.0
    agent_exit: int | None = None
    timed_out: bool = False
    error: str | None = None
    usage: Usage = field(default_factory=Usage)
    outcome: Outcome = field(default_factory=Outcome)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> "Run":
        return cls(
            **{k: v for k, v in data.items() if k not in ("usage", "outcome")},
            usage=Usage(**data.get("usage", {})),
            outcome=Outcome(**data.get("outcome", {})),
        )


def run_id(task: str, variant: str, agent: str, rep: int) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", f"{task}-{variant}-{agent}-{rep}")


def load_runs(results: Path) -> list[Run]:
    path = results / RUNS_FILE
    if not path.is_file():
        raise FileNotFoundError(f"No {RUNS_FILE} in {results}. Pass a directory `run` wrote.")
    return [Run.from_dict(json.loads(line)) for line in path.read_text().splitlines() if line]
