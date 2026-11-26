# pytest report

When xpt is installed, its pytest plugin replaces pytest's terminal output with a shorter report. Agents read every byte of a test run, and pytest's header, progress dots, rules, and full tracebacks cost tokens without saying more.

```text
FAILED tests/test_parser.py::test_parser  tests/test_parser.py:14
  > expect(parse(source), tree)
  a81f  mismatch
  value.operator: "addition" -> "subtraction"
  1 changed, 42 unchanged
  accept: xpt accept a81f
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
- An `expect` failure shows its id, its status, and the diff by path. When the call site can be accepted, the block ends with the command that accepts it, `accept: xpt accept <id>`.
- A plain `assert left == right` on values xpt can record gets the same diff, labeled `left` and `right`.
- Any other exception is one line, `Type: message`, cut to 200 characters, plus a `caused by:` line for a chained exception.
- Parametrized cases that fail the same way fold into one block with an `also fails:` line per other case.
- A failing test prints the last 5 lines of its captured stdout, stderr, and log.
- Setup and teardown errors say `ERROR` and name the phase.

The footer is pytest's counts and duration, then what `--expect-create` and `--expect-accept` wrote, then the next command: `Next: xpt accept <ids>` when there are call sites to accept, `Next: xpt failures` when there are expect failures but none can be accepted.

The `accept:` line accepts one call site, the one whose diff is right above it. The footer's `Next:` accepts every call site that can be accepted. A call site that is rejected, in conflict, or already accepted by this run has no `accept:` line.

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
| `--expect-accept=ID[,ID...]` | | After the session, accept these call sites. Repeatable. See [Reviewing failures](review.md#pytest-expect-accept). |
| `--expect-no-record` | `XPT_NO_RECORD=1` | Don't record failing `expect` calls under `.xpt/`. |
| `--xpt-report=pytest` | `XPT_REPORT=pytest` | Keep pytest's own output. The default is `xpt`. |
| `--xpt-json=PATH` | | Also write the full report, with no limits, as [JSON](json.md#pytest-xpt-json). |
| `--xpt-deterministic` | | Pin the clock, `random`, and `uuid` for every test, as the `xpt_deterministic` fixture does. See [Deterministic tests](#deterministic-tests). |

## Deterministic tests

A value that holds the current time, a fresh UUID, or a random choice changes on every run, so its expectation can never pass. The `xpt_deterministic` fixture pins those sources for one test, so the value is the same on every run and can be expected directly:

```python
def test_create(xpt_deterministic):
    order = create_order()
    expect(
        order, {"id": "00000000-0000-4000-8000-000000000001", "created_at": "2000-01-01T00:00:00"}
    )


def test_expiry(xpt_deterministic):
    token = issue_token()
    xpt_deterministic.advance(3600)
    expect(token.expired(), True)
```

While the test runs:

- `time.time()`, `time.time_ns()`, `datetime.datetime.now()`, `utcnow()`, `today()`, and `datetime.date.today()` read 2000-01-01 00:00:00 UTC. The clock moves only when the test calls `xpt_deterministic.advance(seconds)`, with a number or a `timedelta`. Naive results are in UTC, so the test reads the same in every time zone, and `now(tz)` converts to `tz`.
- `uuid.uuid4()` returns `00000000-0000-4000-8000-000000000001`, then `...0002`, and so on. `uuid.uuid1()` counts the same way from `00000000-0000-1000-8000-000000000001`. Both are valid UUIDs of their version.
- `random` is seeded from the test's node id, so each test has its own fixed sequence, and two tests don't share one.

`--xpt-deterministic` applies the fixture to every test. Both work under xdist, because the seed comes from the node id and not from the worker. Everything is put back when the test ends, also when it fails.

A module in your project that imported a name directly, as in `from datetime import datetime` or `from uuid import uuid4`, is pinned too. Installed packages and the standard library keep what they imported, and so does C code: a library that did `from time import time` reads the real clock. `time.monotonic()`, `time.perf_counter()`, `time.sleep()`, `time.localtime()`, `os.urandom()`, `secrets`, and `random.SystemRandom` are not pinned, so timeouts and durations still work. The patch is process-wide, so other threads see the pinned clock too.

xpt doesn't record `datetime` or `UUID` objects yet, so expect `value.isoformat()` or `str(value)`, as above.

The flaky report names the fixture when a path that changed between runs looks like a timestamp or a UUID.

Outside pytest, use the context manager. In `unittest`:

```python
from xpt.deterministic import deterministic


class TestOrder(unittest.TestCase):
    def setUp(self):
        self.clock = self.enterContext(deterministic(self.id()))
```

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
