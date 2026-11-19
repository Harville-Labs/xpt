from pathlib import Path

import pytest

from xpt_bench.workspace import (
    WorkspaceError,
    banned_words,
    clean_env,
    neutral_dir,
    neutrality_problems,
    sweep_root,
)


def test_neutrality_finds_banned_words_case_insensitively():
    words = banned_words(["stock"])
    assert neutrality_problems("/tmp/My-Benchmark/Stock", words) == ["bench", "stock"]
    assert neutrality_problems("/tmp/tmpab12/ledger", words) == []


def test_neutral_dir_names_avoid_banned_words(tmp_path: Path):
    words = banned_words([])
    for _ in range(20):
        path = neutral_dir(tmp_path, words)
        assert neutrality_problems(path.name, words) == []


def test_sweep_root_refuses_a_base_path_the_agent_would_see(tmp_path: Path):
    base = tmp_path / "xpt-bench-work"
    base.mkdir()
    with pytest.raises(WorkspaceError, match="--workroot"):
        sweep_root(base, banned_words([]))


def test_clean_env_drops_parent_session_and_report_vars(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/cfg")
    monkeypatch.setenv("XPT_REPORT", "pytest")
    monkeypatch.setenv("VIRTUAL_ENV", "/venv")
    env = clean_env(Path("/cache"), {"XPT_REPORT": "agent"})
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env
    assert "VIRTUAL_ENV" not in env
    assert env["CLAUDE_CONFIG_DIR"] == "/cfg"
    assert env["XPT_REPORT"] == "agent"
    assert env["UV_CACHE_DIR"] == "/cache"
