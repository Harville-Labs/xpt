# pytest report

When xpt is installed, its pytest plugin replaces pytest's terminal output with a shorter report. Agents read every byte of a test run, and pytest's header, progress dots, rules, and full tracebacks cost tokens without saying more.

```text
FAILED tests/test_parser.py::test_parser  tests/test_parser.py:14
  > expect(parse(source), tree)
  a81f  mismatch
  value.operator: "addition" -> "subtraction"
  1 changed, 42 unchanged
FAILED tests/test_api.py::test_totals  tests/test_api.py:30
  > assert totals(order) == {"total": 42, "items": 3, "tax": 0, "due": 42}
  value["total"]: left 41, right 42
  1 changed, 3 unchanged
FAILED tests/test_io.py::test_load  tests/test_io.py:8 > src/app/io.py:12 read_config
  > return json.loads(text)
  json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
  caused by: FileNotFoundError: [Errno 2] No such file or directory: 'app.toml'
6 failed, 2 passed in 0.31s. Next: xpt accept a81f
```

## What each block says

- The header names the test and the deepest frame in a project file, outside `site-packages` and pytest. When the error comes from a helper, it shows the chain of project frames from the test down.
- The next line is the source line at that frame, after `> `.
- An `expect` failure shows its id, its status, and the diff by path.
- A plain `assert left == right` on values xpt can record gets the same diff, labeled `left` and `right`.
- Any other exception is one line, `Type: message`, cut to 200 characters, plus a `caused by:` line for a chained exception.
- Parametrized cases that fail the same way fold into one block with an `also fails:` line per other case.
- A failing test prints the last 5 lines of its captured stdout, stderr, and log.
- Setup and teardown errors say `ERROR` and name the phase.

The footer is pytest's counts and duration, then the next command: `Next: xpt accept <ids>` when there are call sites to accept, `Next: xpt failures` when there are expect failures but none can be accepted.

## Verbosity

| Option | Effect |
|---|---|
| default | 10 changes per expectation, 80 characters per value |
| `-v` | 50 changes, 400 characters per value, 20 output lines, pytest's line per test |
| `-vv` | no limits, plus pytest's traceback |
| `-q` | the same compact report |
| `--no-summary` | the footer only |

## Options

| Option | Environment | Effect |
|---|---|---|
| `--expect-create` | | After the session, fill every missing expectation. See [Reviewing failures](review.md#pytest-expect-create). |
| `--expect-no-record` | `XPT_NO_RECORD=1` | Don't record failing `expect` calls under `.xpt/`. |
| `--xpt-report=pytest` | `XPT_REPORT=pytest` | Keep pytest's own output. The default is `xpt`. |
| `--xpt-json=PATH` | | Also write the full report, with no limits, as [JSON](json.md#pytest-xpt-json). |

## When pytest's report is kept

xpt replaces only pytest's header, progress, failure sections, default short summary, and final line. Everything else still prints: warnings, plugin output, `--durations`, `-x` stops, `--lf` messages, live logs, the debugger, and xdist status lines. The exit code is always pytest's.

These options ask for pytest's own failure output, so the run keeps pytest's report:

- `--tb` with anything but `auto`, `-l`, and `--full-trace`
- runs that don't run tests: `--co`, `--fixtures`, `--fixtures-per-test`, `--markers`, `--setup-only`, `--setup-plan`, `--help`, `--version`

`--xpt-json` with one of these is a usage error, because there is no xpt report to write.

`-r` with anything but the default keeps pytest's short summary beside the xpt report. pytest-sugar, when it is on, keeps its own report.

If the report can't install or render, or pytest is older than 8.0, the run prints pytest's report and one line starting with `xpt:` that says why.

## Compatibility

| | Supported |
|---|---|
| Python | 3.11 and later |
| pytest | 8.0 to 9.2, tested on every minor release |
| pytest-xdist | 3.5 to 3.8. The controller prints one report. |
| unittest | `expect` works without the plugin |

Known gaps: pytest 9.2's terminal progress indicator stays at 0%, and line rewriting on a real terminal is tested only through the code paths a pipe uses.
