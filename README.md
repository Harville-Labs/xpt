# xpt

Inline expect tests for Python, built so coding agents can work with them cheaply and safely.

```python
from xpt import expect


def test_parser():
    expect(parse("x + y"))
```

With xpt installed, `pytest` prints one short block per failure and a footer with the next command, in place of pytest's header, dots, and tracebacks:

```
FAILED tests/test_parser.py::test_parser  tests/test_parser.py:14
  > expect(parse(source), tree)
  a81f  mismatch
  value.operator: "addition" -> "subtraction"
  1 changed, 42 unchanged
1 failed, 12 passed in 0.31s. Next: xpt accept a81f
```

`-v` raises the limits, `-vv` adds the traceback, and `--xpt-json PATH` writes the whole report as JSON. `--xpt-report=pytest`, or `XPT_REPORT=pytest`, keeps pytest's own output.

The report needs pytest 8.0 or later and is tested on every minor release from 8.0 to 9.2, with pytest-xdist 3.5 to 3.8. It replaces only pytest's header, progress, failure sections, default short summary, and final line. Everything else pytest and other plugins print still prints, and the exit code is always pytest's. Options that ask for pytest's own failure output (`--tb` other than `auto`, `-l`, `--full-trace`) and runs that don't run tests (`--co`, `--fixtures`, `--markers`, `--setup-plan`) keep pytest's report. `-r` with anything but the default keeps pytest's short summary beside the xpt report. pytest-sugar, when it is on, keeps its own report. If the xpt report can't install or render, or pytest is older than 8.0, the run prints pytest's report and one line starting with `xpt:` that says why.

A run records what `expect` saw under `.xpt/`. Then:

```sh
xpt failures          # each failed call site with its diff, and the next command
xpt show a81f         # one call site, with its function and tests
xpt accept a81f       # write the actual value into the test source
xpt accept --all      # every acceptable call site that was not rejected
xpt reject a81f       # the actual value is a bug; stop offering it for acceptance
xpt runs              # recorded runs, newest first
xpt clean             # delete .xpt/
```

One command helps a project start:

```sh
xpt init              # add xpt's instructions to AGENTS.md and check pytest loads the plugin
```

`pytest --expect-create` runs the tests, then fills every missing expectation the same way `xpt accept --missing` does. It writes after the session ends, never during it, and never replaces an existing expected value. The footer names what it wrote and suggests `xpt accept` only for what remains:

```
2 failed in 0.25s. Filled 1 missing expectation: test_demo.py:4. Next: xpt accept 3526
```

The run still fails, because the filled expectations were empty when their tests ran. Run the tests again to see them pass.

`xpt accept` writes without running the tests again, and refuses if that source changed since the run. `xpt reject` records in `.xpt/decisions.jsonl` that a call site's actual value is a real bug. `xpt failures` and the pytest footer then mark that call site `rejected` and stop suggesting it, until a run records a different value there. Every command takes `--json`. Exit codes are 0 for done or nothing to do, 1 when failures remain or an accept or reject was refused, and 2 for a usage error. `python -m xpt` works too, and `xpt.api` is the same surface for tools.

Status: alpha. The documentation is at https://harville-labs.github.io/xpt/, and [llms.txt](https://harville-labs.github.io/xpt/llms.txt) indexes it as Markdown for language models. Read [docs/design.md](docs/design.md) for the design and roadmap, and [docs/research.md](docs/research.md) for how expecttest, inline-snapshot, syrupy, and ApprovalTests compare. Plans are tracked as GitHub issues.


[bench/](bench/README.md) measures whether agents fix failing suites with fewer tokens and no more wrong accepts using xpt's report than with pytest's own output.
