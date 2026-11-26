## Tests with xpt

This project uses xpt inline expect tests (https://harville-labs.github.io/xpt/).

- Write new expectations as `expect(value)` with no expected argument, run `pytest --expect-create`, then read the filled value in the diff before committing it.
- When a test fails, read the diff in the pytest output or `xpt failures`. Decide whether the new value is correct.
  - Correct, because the code's behavior was meant to change: run the `accept:` command printed under its diff with `--verify`, `xpt accept --verify <id>`. It reruns only the tests that hit those call sites, so you don't need to run the suite again. To accept while running the tests, use `pytest --expect-accept=<id>`.
  - A bug: fix the code. If you can't fix it now, run `xpt reject <id>` so it isn't accepted by accident.
- Never run `xpt accept --all` without reading every diff `xpt failures` prints first.
- When accept reports `suspect`, the value looks like a failure. Fix the code. Use `xpt accept --force <id>` only when that value is really the intended result.
- A `flaky:` line under a diff means that path changed between runs with no source change, such as a timestamp or a list built from a set. Add the option it suggests (`ignore=`, `unordered=True`, or `atol=`) to the `expect()` call, or the `xpt_deterministic` fixture to the test when it names it, and run the tests again, instead of accepting the value.
- Don't hand-edit an expected value that xpt can write. Accept it, so the value is exactly what the code returned.
- `xpt show <id> --full` prints whole values when the diff is cut.
- Use `--json` on any xpt command when parsing its output.
