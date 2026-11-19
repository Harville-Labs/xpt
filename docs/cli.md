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
| `1` | `failures` found failures, or `accept` or `reject` left a requested call site stale or skipped. |
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
