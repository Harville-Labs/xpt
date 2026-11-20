# CLI reference

```text
xpt {failures,show,accept,reject,runs,clean,init,convert} [options]
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

### `xpt init`

Add xpt's agent instructions to `AGENTS.md` in the project root, then check that pytest will load xpt's plugin.

The instructions are the block in [Coding agents](agents.md#paste-into-agentsmd), between `<!-- xpt:begin -->` and `<!-- xpt:end -->` lines. `xpt init` creates `AGENTS.md` if it is missing and otherwise appends the block after a blank line, in the file's newline style. Running it again changes nothing. It never replaces or duplicates a block that is already there:

- A marked block with the current text is reported as present.
- A marked block that was edited, or came from another xpt version, is kept. To replace it, delete it with its markers and run `xpt init` again.
- A `## Tests with xpt` section pasted by hand without markers is kept.
- Markers that don't pair up are an error, and nothing is written.

The checks report:

- **pytest**: whether pytest 8.0 or later is installed in the interpreter running xpt.
- **plugin**: whether xpt's plugin is installed and pytest will load it. It reads `addopts` from the configuration file pytest would use (`pytest.ini`, `.pytest.ini`, `pyproject.toml`, `tox.ini`, or `setup.cfg`) and from `PYTEST_ADDOPTS`, looking for `-p no:xpt`. With `PYTEST_DISABLE_PLUGIN_AUTOLOAD` set, the plugin must be named with `-p xpt.pytest_plugin`.

| Option | Meaning |
|---|---|
| `--dry-run` | Report what would change and write nothing. The checks still run. |

### `xpt convert PATH [PATH ...]`

Rewrite `assert <expr> == <literal>` as `expect(<expr>, <literal>)` in test files, and add `from xpt import expect` where it is missing. A directory is searched for `test_*.py` and `*_test.py`, skipping hidden directories, `__pycache__`, `node_modules`, and `site-packages`. Every path must be under `--root`.

The literal may be on either side. It is anything `ast.literal_eval` accepts: strings, numbers, `True`, `False`, `None`, and lists, tuples, sets, and dicts of them. The rest of the file keeps its bytes, including comments, newline style, and byte order mark.

An assert is skipped, with its line and reason, when converting it could change what the test checks or lose something:

- It has a message.
- It is a chained comparison, such as `a == b == c`.
- Neither side is a literal, or both are.
- The literal holds a float or complex number, which `==` compares exactly. Convert it by hand, or use `atol=`.
- The literal holds bytes, which xpt cannot record.
- A comment sits inside the assert.
- The file binds the name `expect` to something else.

Asserts that are not an `==` comparison are left alone without a report. A file that is not UTF-8 or does not parse is skipped whole. Files are written the way `xpt accept` writes them: to a temporary file that is moved into place.

| Option | Meaning |
|---|---|
| `--dry-run` | Report what would change and write nothing. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The command did what it was asked, or there was nothing to do. |
| `1` | `failures` found failures, `accept` or `reject` left a requested call site stale or skipped, or an `init` check found a problem. |
| `2` | A usage error, an unknown id or run, no recorded run, a malformed record, a held `.xpt/accept.lock`, a `convert` path that is missing or outside the root, or unpaired markers in `AGENTS.md`. The error is one line on stderr that says what to do next, and nothing is written. |

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
