from xpt_bench.agents import Usage
from xpt_bench.evaluate import Outcome
from xpt_bench.runs import Run


def make_run(
    variant: str,
    rep: int,
    tokens: int,
    solved: bool = True,
    wrong: bool = False,
    task: str = "ledger",
    error: str | None = None,
) -> Run:
    return Run(
        id=f"{task}-{variant}-claude-haiku-{rep}",
        task=task,
        variant=variant,
        agent="claude:haiku",
        rep=rep,
        wall_s=10.0,
        error=error,
        usage=Usage(input_tokens=tokens, output_tokens=0, tool_calls=3, test_runs=1),
        outcome=Outcome(
            suite_passed=solved,
            oracle_passed=solved,
            wrong_accepts=["tests/t.py: bad"] if wrong else [],
        ),
    )
