import json

from helpers import make_run

from xpt_bench.cli import main
from xpt_bench.report import RULE, compare, render, render_json, resolution, spread, summarize
from xpt_bench.runs import Run


def arm(variant: str, tokens: list[int], task: str = "ledger", **kw) -> list[Run]:
    return [make_run(variant, i, t, task=task, **kw) for i, t in enumerate(tokens, 1)]


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


def test_clearly_fewer_tokens_with_equal_quality_is_a_win():
    runs = arm("xpt", [100, 105, 98, 102, 101]) + arm("stock", [200, 210, 190, 205, 198])
    [v] = compare(runs, "stock")
    assert v.verdict == "win"
    assert v.better["tokens"] == "xpt"
    assert v.tokens is not None and v.tokens.high < 1
    assert v.n == (5, 5)


def test_a_lower_median_inside_the_noise_is_a_tie():
    runs = arm("xpt", [80, 120, 97, 90, 105]) + arm("stock", [82, 111, 98, 95, 100])
    [v] = compare(runs, "stock")
    assert v.tokens is not None and v.tokens.estimate < 1 and v.tokens.contains(1.0)
    assert (v.better["tokens"], v.verdict) == ("tie", "tie")


def test_clearly_more_tokens_is_a_loss():
    runs = arm("xpt", [300, 310, 290, 305, 299]) + arm("stock", [200, 210, 190, 205, 198])
    [v] = compare(runs, "stock")
    assert (v.better["tokens"], v.verdict) == ("stock", "loss")


def test_fewer_tokens_with_more_wrong_accepts_is_a_loss_even_inside_the_interval():
    runs = arm("xpt", [100] * 4) + arm("xpt", [100], wrong=True) + arm("stock", [200] * 5)
    [v] = compare(runs, "stock")
    assert v.better["wrong_accepts"] == "tie"
    assert v.verdict == "loss"


def test_fewer_tokens_with_fewer_solves_is_a_loss():
    runs = arm("xpt", [100], solved=False) + arm("stock", [200])
    [v] = compare(runs, "stock")
    assert v.verdict == "loss"


def test_rates_name_the_better_arm_only_when_the_interval_excludes_zero():
    runs = arm("xpt", [100] * 10) + arm("stock", [100] * 10, solved=False)
    [v] = compare(runs, "stock")
    assert v.solved.low > 0
    assert v.better["solved"] == "xpt"
    runs = arm("xpt", [100] * 5) + arm("stock", [100] * 4) + arm("stock", [100], solved=False)
    [v] = compare(runs, "stock")
    assert v.solved.estimate > 0 and v.better["solved"] == "tie"


def test_all_row_takes_the_geometric_mean_of_task_ratios():
    runs = arm("xpt", [50] * 5) + arm("stock", [100] * 5)
    runs += arm("xpt", [400] * 5, task="textkit") + arm("stock", [200] * 5, task="textkit")
    verdicts = {v.task: v for v in compare(runs, "stock")}
    assert set(verdicts) == {"all", "ledger", "textkit"}
    total = verdicts["all"]
    assert total.tokens is not None and abs(total.tokens.estimate - 1) < 1e-9
    assert (total.n, total.verdict) == ((10, 10), "tie")
    assert verdicts["ledger"].verdict == "win"
    assert verdicts["textkit"].verdict == "loss"


def test_compare_is_deterministic_for_a_seed():
    runs = arm("xpt", [80, 120, 97, 90, 105]) + arm("stock", [82, 111, 98, 95, 100])
    assert compare(runs, "stock") == compare(runs, "stock")
    assert compare(runs, "stock", seed=7) == compare(runs, "stock", seed=7)


def test_resolution_scales_reps_with_the_square_of_the_interval_width():
    runs = arm("xpt", [80, 120, 97, 90, 105]) + arm("stock", [82, 111, 98, 95, 100])
    [v] = verdicts = compare(runs, "stock")
    assert v.tokens is not None
    text = resolution(verdicts)
    assert text is not None and text.startswith("Resolution: at 5 runs per arm")
    assert resolution([]) is None


def test_render_states_the_rule_and_shows_intervals():
    runs = [make_run("xpt", 1, 1000), make_run("xpt", 2, 3000), make_run("stock", 1, 2000)]
    text = render(summarize(runs), compare(runs, "stock"))
    assert RULE in text
    assert "2.0k [1.0k..3.0k]" in text
    assert "2/2 [34%..100%]" in text
    assert "+0% [-50%..+50%] tie" in text
    assert "+0pp [" in text


def test_render_json_carries_every_interval():
    runs = arm("xpt", [100, 105, 98, 102, 101]) + arm("stock", [200, 210, 190, 205, 198])
    data = json.loads(render_json(summarize(runs), compare(runs, "stock")))
    [v] = data["verdicts"]
    assert set(v["tokens"]) == {"estimate", "low", "high"}
    assert v["better"]["tokens"] == "xpt" and v["verdict"] == "win"
    assert data["rule"] == RULE


def test_report_command_prints_json(tmp_path, capsys):
    runs = arm("xpt", [100, 105, 98, 102, 101]) + arm("stock", [200, 210, 190, 205, 198])
    (tmp_path / "runs.jsonl").write_text("".join(r.to_json() + "\n" for r in runs))
    assert main(["report", "--json", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["verdicts"][0]["verdict"] == "win"
