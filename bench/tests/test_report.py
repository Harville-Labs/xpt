from helpers import make_run

from xpt_bench.report import RULE, compare, render, spread, summarize


def test_spread_is_median_min_and_max_ignoring_missing_values():
    s = spread([3, None, 1, 2])
    assert s is not None
    assert (s.median, s.low, s.high) == (2, 1, 3)
    assert spread([None]) is None


def test_groups_split_by_task_agent_and_variant_and_skip_errored_runs():
    runs = [
        make_run("xpt", 1, 100),
        make_run("xpt", 2, 300),
        make_run("xpt", 3, 0, error="boom"),
        make_run("stock", 1, 500, solved=False),
    ]
    groups = {g.variant: g for g in summarize(runs)}
    assert (groups["xpt"].n, groups["xpt"].errors, groups["xpt"].solved) == (2, 1, 2)
    assert groups["xpt"].tokens is not None and groups["xpt"].tokens.median == 200
    assert groups["stock"].solved == 0


def test_multiple_tasks_add_an_all_tasks_row():
    runs = [make_run("xpt", 1, 100), make_run("xpt", 1, 100, task="textkit")]
    assert sorted(g.task for g in summarize(runs)) == ["all", "ledger", "textkit"]


def test_fewer_tokens_with_equal_quality_is_a_win():
    runs = [make_run("xpt", 1, 100), make_run("stock", 1, 200)]
    [verdict] = compare(summarize(runs), "stock")
    assert verdict.verdict == "win"
    assert verdict.token_change == -0.5


def test_fewer_tokens_with_more_wrong_accepts_is_a_loss():
    runs = [make_run("xpt", 1, 100, wrong=True), make_run("stock", 1, 200)]
    [verdict] = compare(summarize(runs), "stock")
    assert verdict.verdict == "loss"


def test_fewer_tokens_with_fewer_solves_is_a_loss():
    runs = [make_run("xpt", 1, 100, solved=False), make_run("stock", 1, 200)]
    [verdict] = compare(summarize(runs), "stock")
    assert verdict.verdict == "loss"


def test_more_tokens_with_equal_quality_is_no_gain():
    runs = [make_run("xpt", 1, 300), make_run("stock", 1, 200)]
    [verdict] = compare(summarize(runs), "stock")
    assert verdict.verdict == "no gain"


def test_render_states_the_rule_and_shows_median_with_range():
    runs = [make_run("xpt", 1, 1000), make_run("xpt", 2, 3000), make_run("stock", 1, 2000)]
    groups = summarize(runs)
    text = render(groups, compare(groups, "stock"))
    assert RULE in text
    assert "2.0k [1.0k..3.0k]" in text
    assert "-0%" in text or "+0%" in text
