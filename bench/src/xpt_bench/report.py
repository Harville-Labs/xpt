import json
import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from xpt_bench import stats
from xpt_bench.runs import Run
from xpt_bench.stats import Interval

JUDGE_FILE = "judge.jsonl"
ALL = "all"
RULE = (
    "Rule: tokens, tools, and wall s show the variant's median as a change from the baseline's "
    "with a 95% bootstrap interval; solved, wrong, and weak show the difference in rates with "
    "a 95% Newcombe-Wilson interval. A metric names the better arm only when its interval "
    "excludes no change, and is a tie otherwise. The verdict is a win only when the token "
    "interval lies wholly below no change, a loss when it lies wholly above, and a tie "
    "otherwise. Quality is a guard: a lower solve rate or more wrong accepts or weakened "
    "tests is a loss even inside the interval. The all row is the geometric mean of the "
    "per-task ratios, resampled within each task, not a pool of raw runs."
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
    """One variant against the baseline for one task (or all tasks) and agent.

    `tokens`, `tool_calls`, and `wall_s` are intervals for the ratio of medians (variant over
    baseline). `solved`, `wrong_accepts`, and `weakened` are intervals for the difference in
    rates (variant minus baseline). `better` maps each metric to the better arm's name, or
    "tie".
    """

    task: str
    agent: str
    variant: str
    baseline: str
    n: tuple[int, int]
    tokens: Interval | None
    tool_calls: Interval | None
    wall_s: Interval | None
    solved: Interval
    wrong_accepts: Interval
    weakened: Interval
    better: dict[str, str]
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


RATIOS: dict[str, Callable[[Run], float]] = {
    "tokens": lambda r: r.usage.total_tokens,
    "tool_calls": lambda r: r.usage.tool_calls,
    "wall_s": lambda r: r.wall_s,
}
RATES: dict[str, tuple[Callable[[Run], bool], bool]] = {
    # metric: (counts as a success, higher is better)
    "solved": (lambda r: r.outcome.oracle_passed, True),
    "wrong_accepts": (lambda r: bool(r.outcome.wrong_accepts), False),
    "weakened": (lambda r: bool(r.outcome.weakened), False),
}


def _ratio_side(i: Interval | None, variant: str, baseline: str) -> str:
    if i is None or i.contains(1.0):
        return "tie"
    return variant if i.high < 1 else baseline


def _rate_side(i: Interval, higher_is_better: bool, variant: str, baseline: str) -> str:
    if i.contains(0.0):
        return "tie"
    return variant if (i.low > 0) == higher_is_better else baseline


def _verdict(
    task: str,
    agent: str,
    variant: str,
    baseline: str,
    cells: list[tuple[list[Run], list[Run]]],
    resamples: int,
    seed: int,
) -> Verdict:
    ratios = {
        name: stats.median_ratio(
            [([f(r) for r in a], [f(r) for r in b]) for a, b in cells], resamples, seed
        )
        for name, f in RATIOS.items()
    }
    na = sum(len(a) for a, _ in cells)
    nb = sum(len(b) for _, b in cells)
    rates = {
        name: stats.rate_difference(
            (sum(f(r) for a, _ in cells for r in a), na),
            (sum(f(r) for _, b in cells for r in b), nb),
        )
        for name, (f, _) in RATES.items()
    }
    better = {name: _ratio_side(i, variant, baseline) for name, i in ratios.items()}
    for name, (_, up) in RATES.items():
        better[name] = _rate_side(rates[name], up, variant, baseline)
    worse_quality = (
        rates["solved"].estimate < 0
        or rates["wrong_accepts"].estimate > 0
        or rates["weakened"].estimate > 0
    )
    if worse_quality or better["tokens"] == baseline:
        verdict = "loss"
    elif better["tokens"] == variant:
        verdict = "win"
    else:
        verdict = "tie"
    return Verdict(
        task=task,
        agent=agent,
        variant=variant,
        baseline=baseline,
        n=(na, nb),
        tokens=ratios["tokens"],
        tool_calls=ratios["tool_calls"],
        wall_s=ratios["wall_s"],
        solved=rates["solved"],
        wrong_accepts=rates["wrong_accepts"],
        weakened=rates["weakened"],
        better=better,
        verdict=verdict,
    )


def compare(
    runs: list[Run],
    baseline: str,
    resamples: int = stats.RESAMPLES,
    seed: int = stats.SEED,
) -> list[Verdict]:
    """Compare every variant with the baseline per task and agent, plus an all-tasks row."""
    arms: dict[tuple[str, str, str], list[Run]] = defaultdict(list)
    for run in runs:
        if run.error is None:
            arms[(run.task, run.agent, run.variant)].append(run)
    pairs = sorted({(agent, variant) for _, agent, variant in arms if variant != baseline})
    verdicts = []
    for agent, variant in pairs:
        tasks = sorted(
            task
            for task, a, v in arms
            if a == agent and v == variant and (task, agent, baseline) in arms
        )
        cells = {t: (arms[(t, agent, variant)], arms[(t, agent, baseline)]) for t in tasks}
        rows = [(t, [cells[t]]) for t in tasks]
        if len(tasks) > 1:
            rows.insert(0, (ALL, list(cells.values())))
        verdicts += [
            _verdict(task, agent, variant, baseline, c, resamples, seed) for task, c in rows
        ]
    return sorted(verdicts, key=lambda v: (v.task, v.agent, v.variant))


def resolution(verdicts: list[Verdict], target: float = 0.10) -> str | None:
    """How many runs per arm a sweep needs before a `target` token change is resolvable.

    Uses the median half-width, in log space, of the per-task token intervals, and assumes
    the width shrinks with the square root of the runs per arm.
    """
    cells = [v for v in verdicts if v.task != ALL and v.tokens and v.tokens.low > 0]
    if not cells:
        return None
    half = statistics.median(
        (math.log(v.tokens.high) - math.log(v.tokens.low)) / 2 for v in cells if v.tokens
    )
    n = int(statistics.median(min(v.n) for v in cells))
    reps = max(1, math.ceil(n * (half / math.log(1 + target)) ** 2))
    return (
        f"Resolution: at {n} runs per arm the median per-task token interval is about "
        f"\u00b1{math.exp(half) - 1:.0%}. Resolving a \u00b1{target:.0%} token change needs "
        f"about {reps} runs per arm per task."
    )


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


def _pct(value: float) -> str:
    return f"{round(value * 100):+d}%"


def _ratio_cell(i: Interval | None, side: str) -> str:
    if i is None:
        return "-"
    return f"{_pct(i.estimate - 1)} [{_pct(i.low - 1)}..{_pct(i.high - 1)}] {_side(side)}"


def _pp(value: float) -> str:
    return f"{round(value * 100):+d}pp"


def _rate_cell(i: Interval, side: str) -> str:
    return f"{_pp(i.estimate)} [{_pp(i.low)}..{_pp(i.high)}] {_side(side)}"


def _side(side: str) -> str:
    return side if side == "tie" else f"{side} better"


def _count_cell(count: int, n: int) -> str:
    w = stats.wilson(count, n)
    return f"{count}/{n} [{w.low:.0%}..{w.high:.0%}]"


def render(groups: list[Group], verdicts: list[Verdict]) -> str:
    header = ["task", "agent", "variant", "n", "solved", "wrong", "weak", "tokens", "output"]
    header += ["cost", "tools", "test runs", "wall s", "judge"]
    rows = [
        [
            g.task,
            g.agent,
            g.variant,
            str(g.n) + (f" (+{g.errors} err)" if g.errors else ""),
            _count_cell(g.solved, g.n),
            _count_cell(g.wrong_accepts, g.n),
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
    out = [
        "Median [min..max] per task, agent, and variant. Solved and wrong show the count "
        "with a 95% Wilson interval for the rate.",
        _table(header, rows),
        "",
    ]
    if verdicts:
        vrows = [
            [
                v.task,
                v.agent,
                f"{v.variant} vs {v.baseline}",
                f"{v.n[0]} vs {v.n[1]}",
                _ratio_cell(v.tokens, v.better["tokens"]),
                _ratio_cell(v.tool_calls, v.better["tool_calls"]),
                _ratio_cell(v.wall_s, v.better["wall_s"]),
                _rate_cell(v.solved, v.better["solved"]),
                _rate_cell(v.wrong_accepts, v.better["wrong_accepts"]),
                _rate_cell(v.weakened, v.better["weakened"]),
                v.verdict,
            ]
            for v in verdicts
        ]
        vheader = ["task", "agent", "variant", "n", "tokens", "tools", "wall s", "solved"]
        vheader += ["wrong", "weak", "verdict"]
        out += [
            "Variant against baseline: change in median or rate (pp) [95% interval], better arm.",
            _table(vheader, vrows),
            "",
            RULE,
        ]
        res = resolution(verdicts)
        if res:
            out.append(res)
    return "\n".join(out)


def render_json(groups: list[Group], verdicts: list[Verdict]) -> str:
    return json.dumps(
        {
            "groups": [asdict(g) for g in groups],
            "verdicts": [asdict(v) for v in verdicts],
            "rule": RULE,
            "resolution": resolution(verdicts),
        },
        indent=2,
    )


def load_judge(results: Path) -> dict[str, float]:
    path = results / JUDGE_FILE
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    return {row["run_id"]: float(row["total"]) for row in rows}
