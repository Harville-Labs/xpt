import pytest

from xpt_bench.catalog import CatalogError, load_tasks, load_variants, parse_agent
from xpt_bench.workspace import banned_words, neutrality_problems


def test_agent_spec_parses_kind_and_optional_model():
    assert parse_agent("claude:sonnet").label == "claude:sonnet"
    assert parse_agent("codex").model is None
    assert parse_agent("codex:default").label == "codex:default"


def test_unknown_agent_is_rejected_with_the_choices():
    with pytest.raises(CatalogError, match="claude, codex"):
        parse_agent("gpt:4")


def test_unknown_task_is_rejected_with_the_choices():
    with pytest.raises(CatalogError, match="ledger"):
        load_tasks("nope")


def test_every_task_has_a_project_check_and_solution():
    for task in load_tasks():
        assert task.project("project").is_dir()
        assert any(task.check.glob("*.py"))
        assert task.solution.is_dir()
        assert task.wrong_accepts


def test_shipped_variants_differ_only_in_report_env():
    variants = {v.name: v for v in load_variants()}
    assert variants["xpt"].env == {}
    assert variants["stock"].env == {"XPT_REPORT": "pytest"}


def test_prompts_name_no_framing_word_variant_or_model():
    words = banned_words([v.name for v in load_variants()] + ["sonnet", "haiku", "opus"])
    for task in load_tasks():
        assert neutrality_problems(task.prompt, words) == [], task.name


def test_project_files_are_free_of_framing_words():
    words = banned_words([])
    for task in load_tasks():
        for path in task.project("project").rglob("*"):
            rel = path.relative_to(task.project("project")).as_posix()
            assert neutrality_problems(rel, words) == [], rel
            if path.is_file():
                assert neutrality_problems(path.read_text(), words) == [], rel
