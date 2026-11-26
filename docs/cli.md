# CLI reference

```text
xpt {failures,show,accept,reject,runs,flaky,clean,init,convert,run,mcp} [options]
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

Accept also refuses, as `suspect` with the rule `flaky`, a value whose paths changed between stored runs with no source change (see [`xpt flaky`](#xpt-flaky)). The reason names the paths and the options that would keep them stable:

```text
7f3a  tests/test_order.py:7  suspect
  value["created_at"], value["tags"] changed between 2 runs with no source change (rule: flaky). Consider ignore=['value["created_at"]'], unordered=True, the xpt_deterministic fixture, or accept it with --force if it is right.
```

Add the options to the test and run it again, or pass `--force` with the ids when the value is right.

### `xpt reject ID [ID ...]`

Record actual values as bugs, so xpt stops offering them for acceptance.

### `xpt runs`

List recorded runs, newest first.

### `xpt flaky`

List the call sites whose actual value changed between stored runs while the source stayed the same, with the paths that changed and the option that would keep each one stable. It exits `1` when it finds one.

```text
7f3a  tests/test_order.py:7  flaky  2 runs
  flaky: value["created_at"] looks like a timestamp and had 2 values in 2 runs with no source change. Consider ignore=['value["created_at"]'] or the xpt_deterministic fixture.
  flaky: value["tags"] had its items in 2 orders in 2 runs with no source change. Consider unordered=True.
1 flaky call site in 2 stored runs. Next: add the suggested options to the tests, then run them again.
```

Two runs count when they recorded the call site with the same expected argument and the same project source. The pytest plugin stores a digest of every `.py` file under the root with each run, so a value that changed because the code changed is not called flaky. Any edit starts the comparison over.

| Kind | The values at the path | Suggestion |
|---|---|---|
| `timestamp` | Strings that look like a date or time. | `ignore=['value["created_at"]']`, or the [`xpt_deterministic`](pytest.md#deterministic-tests) fixture. |
| `uuid` | Strings that look like a UUID. | `ignore=['value["id"]']`, or the `xpt_deterministic` fixture. |
| `value` | Any other change, such as an id. | `ignore=` the path, with list indexes as `[...]`. A path that is not a dict entry can't be ignored, so make it deterministic in the test. |
| `order` | A list with the same items in another order. | `unordered=True` |
| `float` | Numbers that differ by a little. | `atol=` the spread, rounded up. |

`xpt failures`, `xpt show`, and the pytest report print the same `flaky:` lines under the call site's diff, and leave the call site out of `Next: xpt accept`.

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

With `--mcp`, it also adds the `xpt mcp` server to `.mcp.json` in the project root, creating the file if it is missing:

```json
{"mcpServers": {"xpt": {"command": "xpt", "args": ["mcp"]}}}
```

Other keys and servers in an existing file are kept, and the file is rewritten with two-space indentation. An `xpt` entry that differs is kept and reported, never replaced. A `.mcp.json` that is not a JSON object with an object under `mcpServers` is an error, and neither file is written.

| Option | Meaning |
|---|---|
| `--dry-run` | Report what would change and write nothing. The checks still run. |
| `--mcp` | Also add the `xpt mcp` server to `.mcp.json`. |

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

### `xpt run [-v] -- COMMAND [ARG ...]`

Run a command so that every Python process it starts prints uncaught exceptions in the compact shape of the pytest report, described in [Tracebacks outside pytest](traceback.md). It sets `XPT_TRACEBACK` and puts a `sitecustomize` module that installs the hooks first on `PYTHONPATH`. The exit code is the command's, and 127 when it can't be started. `--json` and `--root` do not apply.

| Option | Meaning |
|---|---|
| `-v` | Raise the limits as `-v` does under pytest. |
| `-vv` | Keep Python's full traceback. |

### `xpt mcp`

Serve `failures`, `show`, `accept`, `reject`, `runs`, and `flaky` as MCP tools over stdio, for MCP clients such as Claude Code and Codex. Each tool returns the object its command's `--json` prints. See [MCP server](mcp.md). It runs until stdin closes. `--json` does not apply.

| Option | Meaning |
|---|---|
| `--root ROOT` | The project root the tools read and write. Default `.`. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The command did what it was asked, or there was nothing to do. |
| `1` | `failures` found failures, `flaky` found flaky call sites, `accept` or `reject` left a requested call site stale, skipped, or suspect, the tests that `accept --verify` reran failed, or an `init` check found a problem. |
| `2` | A usage error, such as `xpt run` without a command, an unknown id or run, no recorded run, a malformed record, a held `.xpt/accept.lock`, a `convert` path that is missing or outside the root, unpaired markers in `AGENTS.md`, or a malformed `.mcp.json` with `init --mcp`. The error is one line on stderr that says what to do next, and nothing is written. |

## Files

```text
.xpt/
  .gitignore            "*", written on first use
  runs/<run>/<worker>.jsonl
  runs/<run>/source     the project source digest, for xpt flaky
  latest                the id of the newest complete run
  decisions.jsonl       rejections, one per line
  accept.lock           held while accept or reject writes
```

Each pytest process, the controller and each xdist worker, writes its own file, so nothing is shared during a run. The last five runs are kept.
