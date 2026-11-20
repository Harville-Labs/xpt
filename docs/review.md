# Reviewing failures

A pytest run records every failed `expect` call under `.xpt/`. The `xpt` command reads that record, so you can review and accept without running the tests again.

## Call sites and ids

Failures are grouped by call site, the `expect` call in the source. A call inside a loop or a parametrized test is one call site that ran several times.

Each call site has a short id such as `b211`. The id is a hash of the file, the function, and the call's position among the `expect` calls in that function, so it stays the same across runs when lines move. It gets longer, like a git short hash, when two ids collide.

## Statuses

| Status | Meaning | Acceptable |
|---|---|---|
| `missing` | `expect(actual)` with no expected value | yes |
| `mismatch` | The values differ | yes |
| `conflict` | The call ran more than once with different values, or the file changed during the run | no |
| `unsupported` | The value has a type xpt can't record, or is over 1 MB | no |
| `unlocatable` | xpt can't find the call in the source | no |

A conflict usually means the call needs one `expect` per case. A call site that you rejected also shows `rejected` after its status.

## `xpt failures`

Lists every failed call site of the latest run with its diff, and ends with the next command.

```text
b211  tests/test_demo.py:18  mismatch  1 test
  value.items[1]: added "pear"
  1 changed, 3 unchanged
ee17  tests/test_demo.py:22  missing  1 test
  actual: {"status": "ok", "count": 3}
2 failures in run 20261119T150648-5031 (1 mismatch, 1 missing), 2 acceptable. Next: xpt accept b211 ee17
```

Plain `assert` failures appear in the pytest report but not here, because xpt can't write them.

## `xpt show ID`

Shows one call site with its function and the tests that hit it. `--full` lifts the limits on how many changes and how much of each value it prints, and adds both whole values.

```text
b211  tests/test_demo.py:18  mismatch  1 test
  function: test_order
  test: tests/test_demo.py::test_order
  value.items[1]: added "pear"
  1 changed, 3 unchanged
Next: xpt accept b211
```

## `xpt accept`

Writes each call site's actual value into the source as its expected value.

```sh
xpt accept b211 ee17      # these call sites
xpt accept --missing      # only empty expectations
xpt accept --all          # every acceptable call site that was not rejected
xpt accept --dry-run b211 # report what would change, write nothing
xpt accept --force b211   # also accept a value that looks broken
```

`xpt accept` with no ids and no flag does nothing and says so.

Accept is careful about what it writes.

- It checks that the expected argument's text still matches the run, and refuses with `stale` if you edited it since. Run the tests again to record a fresh value.
- It refuses with `suspect` a value that looks like a failure: an exception repr or traceback, `None` where there was data, a collection that became empty, or a new `nan`. The reason names the path and the rule. Pass `--force` with the ids when the value is right. See the [CLI reference](cli.md).
- Lines added or removed elsewhere in the file don't matter.
- A second accept of the same value is a no-op and reports `already_accepted`.
- It rewrites each file once, through a temporary file, under `.xpt/accept.lock`. The new text must compile before it is written.
- It reprints the call the way `ruff format` would at line width 100, and leaves the rest of the file byte for byte. A call with a comment inside it keeps the comment, and only the expected argument is replaced.
- It refuses files that aren't UTF-8.

## `xpt reject`

```sh
xpt reject b211
```

Use it when the diff shows a bug rather than a new expectation. It records the call site and a hash of the actual value in `.xpt/decisions.jsonl`. From then on, `xpt failures` and the pytest footer mark that call site `rejected` and stop suggesting `xpt accept` for it. `--all` and `--missing` skip it.

A rejection stops matching as soon as a run records a different value at that call site. The test keeps failing until the code is fixed. Accepting a rejected call site by its id still works.

## `pytest --expect-create`

Runs the tests, then fills every missing expectation the way `xpt accept --missing` does. It writes after the session ends, never during it, and never replaces an existing expected value. The run still fails, because the expectations were empty when their tests ran. Run again to see them pass.

## `pytest --expect-accept`

Runs the tests, then accepts the named call sites the way `xpt accept <id>` does. Use it when you already know the ids, or to accept and see the report in one command.

```sh
pytest --expect-accept=b211,ee17
pytest --expect-accept=b211 --expect-accept=ee17
```

Like `--expect-create`, it writes after the session ends, so the run still fails for those tests. The footer names what was written, `Accepted 2 expectations: tests/test_demo.py:18 tests/test_demo.py:22.`, and what was not, `Not accepted: b211 stale.` with the reason on the lines above it. An id that names no failure in this run is reported as `unknown`. The other ids are still accepted.

Ids come from the report, where each acceptable failure ends with `accept: xpt accept <id>`. An id is stable across runs, so an id from the last run works. With `--expect-create` as well, the named call sites are accepted and the other missing expectations are filled.

Both options need the run to be recorded, so they are usage errors with `--expect-no-record` or `XPT_NO_RECORD`.

## Other commands

- `xpt runs` lists the recorded runs, newest first. xpt keeps the last five.
- `xpt clean` deletes `.xpt/` and every recorded value.
- `--run ID` on `failures`, `show`, `accept`, and `reject` reads an older run.

See the [CLI reference](cli.md) for every option and exit code.
