# Coding agents

xpt is built for agents that run tests, read the output, and change code. Three things matter for that.

- **The report is short.** One block per failure with the diff by path, and a footer that names the next command. An agent spends its tokens on what changed.
- **Accepting is explicit.** A run never writes to the source. `xpt accept` writes only the call sites it is given, refuses when the expected argument changed since the run, and `xpt reject` stops a bug from being offered again.
- **Everything has JSON.** Every command takes `--json`, and `pytest --xpt-json=PATH` writes the full report.

The risk with any snapshot tool is an agent that accepts a wrong value to make a test pass. xpt can't decide whether a value is right. It makes the decision visible: every diff is on screen before the footer suggests an accept, and a rejected value stays rejected.

## Paste into AGENTS.md

`xpt init` writes this block into `AGENTS.md` between `<!-- xpt:begin -->` and `<!-- xpt:end -->` markers and checks that pytest loads xpt's plugin. Running it again changes nothing. To paste it by hand instead:

```markdown
## Tests with xpt

This project uses xpt inline expect tests (https://harville-labs.github.io/xpt/).

- Write new expectations as `expect(value)` with no expected argument, run `pytest --expect-create`, then read the filled value in the diff before committing it.
- When a test fails, read the diff in the pytest output or `xpt failures`. Decide whether the new value is correct.
  - Correct, because the code's behavior was meant to change: `xpt accept <id>`.
  - A bug: fix the code. If you can't fix it now, run `xpt reject <id>` so it isn't accepted by accident.
- Never run `xpt accept --all` without reading every diff `xpt failures` prints first.
- Don't hand-edit an expected value that xpt can write. Accept it, so the value is exactly what the code returned.
- `xpt show <id> --full` prints whole values when the diff is cut.
- Use `--json` on any xpt command when parsing its output.
```

xpt's tests check that this block and the text `xpt init` writes are the same.

## Moving existing tests to xpt

`xpt convert tests` rewrites `assert value == literal` as `expect(value, literal)`, so later changes show as a diff an agent can accept. It skips asserts it can't convert without changing their meaning, such as float literals or asserts with a message, and lists each with its line and reason. Run it with `--dry-run` first.

## Reading the footer

| Footer | Meaning |
|---|---|
| `Next: xpt accept b211 ee17` | These call sites can be accepted. Read their diffs first. |
| `Next: xpt failures` | There are expect failures, but none can be accepted. `xpt failures` says why. |
| `Next: run the tests.` | `--expect-create` filled every missing expectation. |
| no `Next:` | Every failure is a plain assert or exception. Fix the code. |

## LLM-readable docs

This site is published as Markdown for models. [llms.txt](llms.txt) is an index, and [llms-full.txt](llms-full.txt) holds every page in one file.
