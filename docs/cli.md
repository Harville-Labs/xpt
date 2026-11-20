# CLI reference

```text
xpt {failures,show,accept,reject,runs,clean} [options]
```

`python -m xpt` runs the same command.

## Common options

| Option | Commands | Meaning |
|---|---|---|
| `--json` | all | Print one JSON object on stdout. See [JSON output](json.md). |
| `--root ROOT` | all | The project root. Default `.`. |
| `--run RUN` | `failures`, `show`, `accept`, `reject` | A run id from `xpt runs`. Default the latest run. |

## Commands

### `xpt failures`

List the failed call sites of a run, with their diffs.

### `xpt show ID`

Show one call site with its function and tests.

| Option | Meaning |
|---|---|
| `--full` | Lift the change and length limits and print both whole values. |

### `xpt accept [ID ...]`

Write actual values into the test source.

| Option | Meaning |
|---|---|
| `--all` | Every acceptable call site that was not rejected. |
| `--missing` | Only empty expectations. |
| `--dry-run` | Report what would change and write nothing. |
| `--force` | Also accept values that look broken. See below. |
| `--verify` | After writing, rerun only the tests that observed the accepted call sites, and report whether they pass. |

`--verify` runs `python -m pytest <node ids>` with the interpreter that runs xpt, from the project root. The node ids come from the run record, so tests that recorded nothing at those call sites are not run. When they pass, xpt prints `Verified: the 2 accepted tests pass.` When they fail, it prints pytest's output and exits `1`. When nothing was written, nothing is rerun. The rerun records a run like any other test run, so it becomes the latest run. `--verify` can't be combined with `--dry-run`.

Accept refuses an actual value that looks like a failure rather than a result, and reports it `suspect` with the path and the rule that matched:

| Rule | The actual value has |
|---|---|
| `exception` | A string that looks like an exception repr or a traceback, such as `ValueError('bad')` or `KeyError: 'id'`, where the expected value did not. |
| `none` | `None` where the expected value held data. |
| `empty` | An empty list, tuple, set, or dict where the expected value had items. |
| `nan` | `nan` where the expected value was a finite number. |

A missing expectation has no expected value, so only the `exception` rule applies to it. `--all`, `--missing`, and `pytest --expect-create` refuse the same values. Fix the code under test, or pass `--force` with the ids when the value is right.

### `xpt reject ID [ID ...]`

Record actual values as bugs, so xpt stops offering them for acceptance.

### `xpt runs`

List recorded runs, newest first.

### `xpt clean`

Delete `.xpt/` and every recorded value.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The command did what it was asked, or there was nothing to do. |
| `1` | `failures` found failures, or `accept` or `reject` left a requested call site stale, skipped, or suspect, or the tests that `accept --verify` reran failed. |
| `2` | A usage error, an unknown id or run, no recorded run, a malformed record, or a held `.xpt/accept.lock`. The error is one line on stderr that says what to do next, and nothing is written. |

## Files

```text
.xpt/
  .gitignore            "*", written on first use
  runs/<run>/<worker>.jsonl
  latest                the id of the newest complete run
  decisions.jsonl       rejections, one per line
  accept.lock           held while accept or reject writes
```

Each pytest process, the controller and each xdist worker, writes its own file, so nothing is shared during a run. The last five runs are kept.
