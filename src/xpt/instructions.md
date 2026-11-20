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
