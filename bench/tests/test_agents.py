from pathlib import Path

from xpt_bench.agents import command, parse_output
from xpt_bench.catalog import AgentSpec

SAMPLES = Path(__file__).parent / "samples"


def _parse(kind: str):
    with (SAMPLES / f"{kind}.jsonl").open() as lines:
        return parse_output(kind, lines)


def test_claude_usage_comes_from_the_result_event():
    usage = _parse("claude")
    assert (usage.input_tokens, usage.output_tokens) == (138, 4737)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (396283, 17857)
    assert usage.cost_usd == 0.0991653
    assert usage.turns == 17
    assert not usage.is_error


def test_claude_tool_calls_count_each_tool_use_once_and_pytest_runs_separately():
    usage = _parse("claude")
    assert usage.tool_calls == 16
    assert usage.test_runs == 2


def test_codex_input_tokens_exclude_cached_tokens():
    usage = _parse("codex")
    assert usage.input_tokens == 36168 - 29824
    assert usage.cache_read_tokens == 29824
    assert usage.output_tokens == 85
    assert usage.total_tokens == 36168 + 85


def test_codex_counts_commands_and_keeps_the_last_message():
    usage = _parse("codex")
    assert (usage.tool_calls, usage.test_runs) == (1, 1)
    assert usage.turns is None
    assert usage.final_message.endswith("3 passed.")


def test_output_without_a_result_is_an_error():
    assert parse_output("claude", ['{"type": "system", "subtype": "init"}']).is_error
    assert parse_output("codex", ['{"type": "turn.started"}']).is_error


def test_garbage_lines_are_ignored():
    usage = parse_output(
        "codex", ["not json", "", *(SAMPLES / "codex.jsonl").read_text().splitlines()]
    )
    assert usage.output_tokens == 85


def test_claude_command_disables_plugins_and_web_tools():
    cmd = command(AgentSpec("claude", "haiku"), "fix it", Path("/w"), Path("/c"))
    assert cmd[:3] == ["claude", "-p", "fix it"]
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd
    assert "WebFetch" in cmd[cmd.index("--disallowedTools") + 1 :]
    assert cmd[-2:] == ["--model", "haiku"]


def test_codex_command_writes_only_to_the_workdir_and_cache():
    cmd = command(AgentSpec("codex", None), "fix it", Path("/w"), Path("/c"))
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert cmd[cmd.index("-C") + 1] == "/w"
    assert cmd[cmd.index("--add-dir") + 1] == "/c"
    assert "-m" not in cmd
    assert cmd[-1] == "fix it"
