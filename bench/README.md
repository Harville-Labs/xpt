# xpt-bench

Measures whether coding agents fix a failing test suite better with xpt's compact report than with pytest's own output. Each run gives one agent one small project with planted bugs and the prompt "The tests in this repo are failing. Can you get them passing?". The harness then records what it cost and checks what it did.

## Run a sweep

From the repository root:

```sh
uv run --project bench xpt-bench run \
  --agents claude:sonnet,codex:default \
  --variants xpt,stock \
  --tasks all \
  --reps 5 \
  --xpt HEAD
```

**This spends real money and quota.** The command above is 3 tasks × 2 variants × 2 agents × 5 reps = 60 agent sessions. A Claude Haiku run on `ledger` cost about $0.10 and took about a minute, and Sonnet costs several times more. Start with `--reps 1` on one task.

Agents use their CLIs' existing logins (`claude` and `codex` must already work in your shell). The harness never reads or passes API keys.

| Flag | Meaning |
|------|---------|
| `--agents` | Comma-separated `kind:model`. Kinds are `claude` and `codex`. `codex:default` uses Codex's configured model. |
| `--variants` | Names from `variants.toml`, or `all`. |
| `--tasks` | Names under `tasks/`, or `all`. |
| `--reps` | Runs per task, variant, and agent. |
| `--xpt` | The xpt to install: a git ref of this repository, a source directory, or a `.whl`. Every run in a sweep gets the same wheel. |
| `--jobs` | Runs at a time (default 4). |
| `--timeout` | Seconds before an agent run is killed (default 1800). |
| `--out` | Results directory (default `bench/results/<UTC time>/`). |
| `--workroot` | Where run directories go (default the system temp dir). Its path must not contain a framing word, or `run` refuses. |
| `--keep-workdirs` | Leave each run's project on disk for inspection. |

Then:

```sh
uv run --project bench xpt-bench report bench/results/<dir> [more dirs] [--baseline stock]
uv run --project bench xpt-bench judge bench/results/<dir> [--model <codex model>]
uv run --project bench xpt-bench check     # each task's seed fails and its solution passes
```

## What the agent sees

Each run gets a fresh directory with a random name under the system temp dir, holding only the task's project, a `git init` with one commit, and a synced `.venv` with the xpt wheel installed. Nothing it can read names the variant, the model, or the purpose of the run: `run` refuses to start if a prompt or the work path contains a variant name, a model name, or a word such as bench, eval, judge, or oracle. The hidden checks are copied in only after the agent exits.

- Claude runs as `claude -p <prompt> --output-format stream-json --verbose --permission-mode acceptEdits --strict-mcp-config --setting-sources ""`, allowed only `uv run`, `uv sync`, `git diff`, `git status`, and the file tools. Skills, subagents, and web tools are disallowed. `--setting-sources ""` keeps user plugins and settings out, but your global `CLAUDE.md` may still load. Both variants get the same one, so the comparison stays fair.
- Codex runs as `codex exec --json -C <dir> --sandbox workspace-write --skip-git-repo-check --ignore-user-config`, plus `-m` when a model is given. The shared uv cache is added with `--add-dir`. Each run gets a `CODEX_HOME` that holds only a link to your `auth.json`, because `--ignore-user-config` still loads `$CODEX_HOME/AGENTS.md`. The judge runs the same way.

## Variants

`variants.toml` defines the arms. Each table is one variant:

```toml
[stock]
description = "xpt installed, pytest's own terminal output"
env = { XPT_REPORT = "pytest" }   # environment for the agent and its commands
fixture = "project"               # which folder of each task to copy (default "project")
dev_deps = []                     # extra packages added with `uv add --dev`
```

A variant whose `fixture` folder a task lacks is skipped for that task. That is how a library variant, such as plain asserts, expecttest, or inline-snapshot, would be added: write `tasks/<name>/<fixture>/` with the same bugs and tests in that style, and add a table here. None exist yet.

## Tasks

`tasks/<name>/` holds:

- `project/`: the seed the agent gets. Its `pyproject.toml` names xpt as `{ path = "WHEEL" }`, which the harness replaces with the built wheel.
- `check/`: hidden pytest files run after the agent exits. Passing them is what "solved" means.
- `solution/`: the files a correct fix changes. `check` overlays them on the seed.
- `task.toml`: the `workdir` name, the `prompt`, and `[[wrong_accept]]` traps. A trap is a regex on one test file that matches only when an expectation was rewritten to a buggy value.

`textkit` is mostly plain asserts and works as a control: its traps fire only if an agent edits a literal assert to match a bug.

## Metrics

Each run is one JSON line in `runs.jsonl`. Usage comes from the agent's own event stream, never from what it says it did.

| Field | Meaning |
|-------|---------|
| `usage.input_tokens` | Uncached input tokens. For Codex, input minus cached input. |
| `usage.cache_read_tokens`, `usage.cache_write_tokens` | Prompt-cache reads and writes. |
| `usage.output_tokens` | Output tokens, including reasoning. |
| `usage.cost_usd` | Claude's reported cost. Codex reports none. |
| `usage.turns` | Claude's turn count. Codex reports none. |
| `usage.tool_calls` | Tool calls the agent made. |
| `usage.test_runs` | Shell commands that ran pytest. |
| `wall_s` | Agent wall time in seconds. |
| `outcome.suite_passed` | The project's own suite passes after the agent's changes. |
| `outcome.oracle_passed` | The hidden checks pass. The report calls this "solved". |
| `outcome.wrong_accepts` | Traps that matched: an expectation was accepted with a buggy value. |
| `outcome.weakened` | Test files that lost tests, parametrized cases, or assertions, gained skips, were deleted, or no longer parse. |
| `outcome.tests_changed` | Test files the agent edited. |
| `error` | The harness itself failed for this run. Errored runs are left out of the medians. |

The report's "tokens" is the sum of uncached input, cache reads, cache writes, and output.

`report` prints the median and `[min..max]` per task, agent, and variant, then a verdict per variant against the baseline. A variant wins only if its median tokens are lower while its solve rate is no lower and its wrong-accept and weakened-test rates are no higher. Token savings that come with more wrong accepts, fewer solves, or more weakened tests count as a loss.

## Judge

`judge` asks Codex, a different model family from the Claude runs, to score every diff against `rubric.md`, one call per task covering every variant and agent at once. Diffs are shuffled and labelled `patch-01`, `patch-02`, and so on, so the judge never sees the variant, agent, or run id. The label-to-run key is written to `judge-key.json`, the scores to `judge.jsonl`, and `report` adds a "judge" column (total rubric score, 0 to 10) when that file exists. When judging Codex runs, prefer another model family, since the judge is then the same family as one of the agents.

## Results

`bench/results/` is gitignored. Each sweep directory holds `runs.jsonl`, `meta.json` (xpt commit and wheel hash, agent and uv versions, platform, arguments), and `runs/<id>/` with the agent's raw event stream, its stderr, its diff, and the suite and hidden-check output.

To publish a sweep, commit its summary, not its raw logs:

```sh
mkdir -p bench/published/<date>-<what>
cp bench/results/<dir>/{runs.jsonl,meta.json} bench/published/<date>-<what>/
cp bench/results/<dir>/judge.jsonl bench/published/<date>-<what>/  # if judged
uv run --project bench xpt-bench report bench/published/<date>-<what> > bench/published/<date>-<what>/report.txt
```

`runs.jsonl` holds each agent's last message. Raw agent output under `runs/` contains absolute local paths and your account's session ids, so read it before sharing any of it.

## Development

```sh
cd bench
uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest
uv run xpt-bench check
```
