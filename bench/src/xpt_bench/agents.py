import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from xpt_bench.catalog import AgentSpec

PYTEST = re.compile(r"\bpytest\b")
CLAUDE_ALLOWED = [
    "Bash(uv run:*)",
    "Bash(uv sync:*)",
    "Bash(git diff:*)",
    "Bash(git status:*)",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
]
CLAUDE_DISALLOWED = ["Skill", "Agent", "WebFetch", "WebSearch"]
CODEX_TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search"}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    turns: int | None = None
    tool_calls: int = 0
    test_runs: int = 0
    is_error: bool = False
    final_message: str = ""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


def command(agent: AgentSpec, prompt: str, workdir: Path, cache: Path) -> list[str]:
    if agent.kind == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        cmd += ["--permission-mode", "acceptEdits", "--strict-mcp-config"]
        cmd += ["--setting-sources", ""]
        cmd += ["--allowedTools", *CLAUDE_ALLOWED, "--disallowedTools", *CLAUDE_DISALLOWED]
        return cmd + (["--model", agent.model] if agent.model else [])
    cmd = ["codex", "exec", "--json", "-C", str(workdir), "--add-dir", str(cache)]
    cmd += ["--sandbox", "workspace-write", "--skip-git-repo-check", "--ignore-user-config"]
    return cmd + (["-m", agent.model] if agent.model else []) + [prompt]


def _events(lines: Iterable[str]) -> Iterable[dict]:
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def parse_claude(lines: Iterable[str]) -> Usage:
    tool_ids: set[str] = set()
    tests = 0
    result: dict = {}
    for event in _events(lines):
        if event.get("type") == "result":
            result = event
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") != "tool_use" or block.get("id") in tool_ids:
                continue
            tool_ids.add(block.get("id", f"anon-{len(tool_ids)}"))
            command_text = str(block.get("input", {}).get("command", ""))
            if block.get("name") == "Bash" and PYTEST.search(command_text):
                tests += 1
    models = result.get("modelUsage") or {}
    if models:
        totals = {
            key: sum(int(m.get(key, 0)) for m in models.values())
            for key in (
                "inputTokens",
                "outputTokens",
                "cacheReadInputTokens",
                "cacheCreationInputTokens",
            )
        }
    else:
        usage = result.get("usage") or {}
        totals = {
            "inputTokens": int(usage.get("input_tokens", 0)),
            "outputTokens": int(usage.get("output_tokens", 0)),
            "cacheReadInputTokens": int(usage.get("cache_read_input_tokens", 0)),
            "cacheCreationInputTokens": int(usage.get("cache_creation_input_tokens", 0)),
        }
    return Usage(
        input_tokens=totals["inputTokens"],
        output_tokens=totals["outputTokens"],
        cache_read_tokens=totals["cacheReadInputTokens"],
        cache_write_tokens=totals["cacheCreationInputTokens"],
        cost_usd=result.get("total_cost_usd"),
        turns=result.get("num_turns"),
        tool_calls=len(tool_ids),
        test_runs=tests,
        is_error=bool(result.get("is_error", not result)),
        final_message=str(result.get("result", "")),
    )


def parse_codex(lines: Iterable[str]) -> Usage:
    input_tokens = output = cached = cache_write = tools = tests = 0
    failed = False
    completed = False
    final = ""
    for event in _events(lines):
        kind = event.get("type")
        if kind in ("error", "turn.failed"):
            failed = True
        elif kind == "turn.completed":
            completed = True
            usage = event.get("usage", {})
            input_tokens += int(usage.get("input_tokens", 0))
            cached += int(usage.get("cached_input_tokens", 0))
            cache_write += int(usage.get("cache_write_input_tokens", 0))
            output += int(usage.get("output_tokens", 0))
        elif kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") in CODEX_TOOL_ITEMS:
                tools += 1
            if item.get("type") == "command_execution" and PYTEST.search(item.get("command", "")):
                tests += 1
            if item.get("type") == "agent_message":
                final = item.get("text", "")
    return Usage(
        input_tokens=max(0, input_tokens - cached - cache_write),
        output_tokens=output,
        cache_read_tokens=cached,
        cache_write_tokens=cache_write,
        tool_calls=tools,
        test_runs=tests,
        is_error=failed or not completed,
        final_message=final,
    )


def parse_output(kind: str, lines: Iterable[str]) -> Usage:
    return parse_claude(lines) if kind == "claude" else parse_codex(lines)
