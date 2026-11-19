import json

import pytest
from helpers import make_run

from xpt_bench.judge import JudgeError, blind, build_prompt, parse_scores

RUNS = [make_run(v, rep, 100) for v in ("xpt", "stock") for rep in (1, 2)]
DIFFS = {r.id: f"diff for {r.id}" for r in RUNS}


def test_blind_labels_hide_variant_and_agent():
    entries = blind(RUNS, DIFFS, seed=0)
    assert [e.label for e in entries] == ["patch-01", "patch-02", "patch-03", "patch-04"]
    for entry in entries:
        assert entry.run.variant not in entry.label
        assert "claude" not in entry.label


def test_blind_order_is_stable_for_a_seed_and_independent_of_input_order():
    first = [e.run.id for e in blind(RUNS, DIFFS, seed=7)]
    assert first == [e.run.id for e in blind(list(reversed(RUNS)), DIFFS, seed=7)]


def test_prompt_shows_diffs_by_label_only():
    entries = blind(RUNS, {r.id: "+fix" for r in RUNS}, seed=1)
    prompt = build_prompt("# Rubric", "+ref", entries)
    assert "## patch-04" in prompt
    for run in RUNS:
        assert run.id not in prompt
    assert "xpt" not in prompt and "stock" not in prompt


def test_empty_patch_is_marked_in_the_prompt():
    entries = blind(RUNS[:1], {RUNS[0].id: ""}, seed=0)
    assert "(empty patch: no files changed)" in build_prompt("r", "ref", entries)


def _reply(scores: dict[str, int], labels=("patch-01",)) -> str:
    patches = [
        {
            "label": label,
            "notes": "",
            "scores": [{"criterion": k, "score": v} for k, v in scores.items()],
        }
        for label in labels
    ]
    return json.dumps({"patches": patches})


def test_scores_parse_and_total():
    [score] = parse_scores(_reply({"source_fix": 2, "minimal": 1}), ["patch-01"])
    assert score.total == 3


def test_missing_label_is_an_error():
    with pytest.raises(JudgeError, match="patch-02"):
        parse_scores(_reply({"source_fix": 2}), ["patch-01", "patch-02"])


def test_out_of_range_score_is_an_error():
    with pytest.raises(JudgeError, match=r"0\.\.2"):
        parse_scores(_reply({"source_fix": 3}), ["patch-01"])


def test_unparseable_reply_is_an_error():
    with pytest.raises(JudgeError, match="JSON"):
        parse_scores("not json", ["patch-01"])
