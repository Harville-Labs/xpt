import json
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from xpt_bench.runs import Run

JUDGE_FILE = "judge.jsonl"
RULE = (
    "Rule: a variant beats the baseline only if its median tokens are lower while its solve "
    "rate is no lower and its wrong-accept and weakened-test rates are no higher. Token "
    "savings that come with more wrong accepts, fewer solves, or more weakened tests count "
    "as a loss."
)


@dataclass(frozen=True)
class Spread:
    median: float
    low: float
    high: float


@dataclass(frozen=True)
class Group:
    task: str
    agent: str
    variant: str
    n: int
    errors: int
    solved: int
    wrong_accepts: int
    weakened: int
    tokens: Spread | None
    output_tokens: Spread | None
    cost_usd: Spread | None
    tool_calls: Spread | None
    test_runs: Spread | None
    wall_s: Spread | None
    judge: Spread | None

    def rate(self, count: int) -> float:
        return count / self.n if self.n else 0.0


@dataclass(frozen=True)
class Verdict:
    task: str
    agent: str
    variant: str
    baseline: str
    token_change: float | None
    solved: tuple[float, float]
    wrong_accepts: tuple[float, float]
    weakened: tuple[float, float]
    verdict: str


def spread(values: Sequence[float | None]) -> Spread | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return Spread(statistics.median(present), min(present), max(present))


def _group(key: tuple[str, str, str], runs: list[Run], judge: dict[str, float]) -> Group:
    ok = [r for r in runs if r.error is None]

    def over(metric: Callable[[Run], float | None]) -> Spread | None:
        return spread([metric(r) for r in ok])

    return Group(
        task=key[0],
        agent=key[1],
        variant=key[2],
        n=len(ok),
        errors=len(runs) - len(ok),
        solved=sum(r.outcome.oracle_passed for r in ok),
        wrong_accepts=sum(bool(r.outcome.wrong_accepts) for r in ok),
        weakened=sum(bool(r.outcome.weakened) for r in ok),
        tokens=over(lambda r: r.usage.total_tokens),
        output_tokens=over(lambda r: r.usage.output_tokens),
        cost_usd=over(lambda r: r.usage.cost_usd),
        tool_calls=over(lambda r: r.usage.tool_calls),
        test_runs=over(lambda r: r.usage.test_runs),
        wall_s=over(lambda r: r.wall_s),
        judge=over(lambda r: judge.get(r.id)),
    )


def summarize(runs: list[Run], judge: dict[str, float] | None = None) -> list[Group]:
    buckets: dict[tuple[str, str, str], list[Run]] = defaultdict(list)
    for run in runs:
        buckets[(run.task, run.agent, run.variant)].append(run)
        if len({r.task for r in runs}) > 1:
            buckets[("all", run.agent, run.variant)].append(run)
    return [_group(key, buckets[key], judge or {}) for key in sorted(buckets)]


def compare(groups: list[Group], baseline: str) -> list[Verdict]:
    base = {(g.task, g.agent): g for g in groups if g.variant == baseline}
    verdicts = []
    for g in groups:
        b = base.get((g.task, g.agent))
        if g.variant == baseline or b is None or not g.n or not b.n:
            continue
        change = None
        if g.tokens and b.tokens and b.tokens.median:
            change = g.tokens.median / b.tokens.median - 1
        solved = (g.rate(g.solved), b.rate(b.solved))
        wrong = (g.rate(g.wrong_accepts), b.rate(b.wrong_accepts))
        weak = (g.rate(g.weakened), b.rate(b.weakened))
        if solved[0] < solved[1] or wrong[0] > wrong[1] or weak[0] > weak[1]:
            verdict = "loss"
        elif change is not None and change < 0:
            verdict = "win"
        else:
            verdict = "no gain"
        verdicts.append(
            Verdict(g.task, g.agent, g.variant, baseline, change, solved, wrong, weak, verdict)
        )
    return verdicts


def _num(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:.2f}" if value != int(value) else str(int(value))


def _cell(s: Spread | None, money: bool = False) -> str:
    if s is None:
        return "-"
    fmt = (lambda v: f"${v:.2f}") if money else _num
    return f"{fmt(s.median)} [{fmt(s.low)}..{fmt(s.high)}]"


def _table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in [header, *rows]]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(line.rstrip() for line in lines)


def render(groups: list[Group], verdicts: list[Verdict]) -> str:
    header = ["task", "agent", "variant", "n", "solved", "wrong", "weak", "tokens", "output"]
    header += ["cost", "tools", "test runs", "wall s", "judge"]
    rows = [
        [
            g.task,
            g.agent,
            g.variant,
            str(g.n) + (f" (+{g.errors} err)" if g.errors else ""),
            f"{g.solved}/{g.n}",
            str(g.wrong_accepts),
            str(g.weakened),
            _cell(g.tokens),
            _cell(g.output_tokens),
            _cell(g.cost_usd, money=True),
            _cell(g.tool_calls),
            _cell(g.test_runs),
            _cell(g.wall_s),
            _cell(g.judge),
        ]
        for g in groups
    ]
    out = ["Median [min..max] per task, agent, and variant.", _table(header, rows), ""]
    if verdicts:
        vrows = [
            [
                v.task,
                v.agent,
                f"{v.variant} vs {v.baseline}",
                "-" if v.token_change is None else f"{v.token_change:+.0%}",
                f"{v.solved[0]:.0%} vs {v.solved[1]:.0%}",
                f"{v.wrong_accepts[0]:.0%} vs {v.wrong_accepts[1]:.0%}",
                f"{v.weakened[0]:.0%} vs {v.weakened[1]:.0%}",
                v.verdict,
            ]
            for v in verdicts
        ]
        vheader = ["task", "agent", "variant", "tokens", "solved", "wrong", "weak", "verdict"]
        out += [_table(vheader, vrows), "", RULE]
    return "\n".join(out)


def load_judge(results: Path) -> dict[str, float]:
    path = results / JUDGE_FILE
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    return {row["run_id"]: float(row["total"]) for row in rows}
