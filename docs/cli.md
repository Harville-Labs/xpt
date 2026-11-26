# CLI reference

```text
xpt {failures,show,accept,reject,runs,flaky,clean,init,convert,coverage,run,mcp,env,api,probe} [options]
```

`python -m xpt` runs the same command.

## Common options

| Option | Commands | Meaning |
|---|---|---|
| `--json` | all | Print one JSON object on stdout. See [JSON output](json.md). |
| `--root ROOT` | all but `api` and `run` | The project root. Default `.`. |
| `--run RUN` | `failures`, `show`, `accept`, `reject` | A run id from `xpt runs`. Default the latest run. |
| `--since REF` | `show`, `accept`, `coverage` | The git ref to diff the changed code against. Default the merge base with the default branch, or `HEAD`. See [Changed code](#changed-code). |

## Commands

### `xpt failures`

List the failed call sites of a run, with their diffs.

### `xpt show ID`

Show one call site with its function and tests.

| Option | Meaning |
|---|---|
| `--full` | Lift the change and length limits and print both whole values. |

#### Changed code

When the run was traced with `pytest --xpt-trace`, `xpt show` also prints the changed lines its tests ran, grouped by function:

```text
7f3a  tests/test_price.py:14  mismatch  1 test
  function: test_total
  test: tests/test_price.py::test_total
  value["total"]: 110.0 -> 99.0
  1 changed, 3 unchanged
  changed code this test ran (since main):
    src/shop/price.py:31 apply_discount
      - rate = 0.1
      + rate = 0.2
Next: xpt accept 7f3a
```

- The diff is `git diff` against `--since REF`, or by default the merge base of `HEAD` with the default branch (`origin/HEAD`, `main`, or `master`), or `HEAD` when there is none. Staged, unstaged, and untracked `.py` files count.
- When the tests ran no changed line, it prints `no changed code ran; the expectation or the environment changed (diff against main)`. The expectation is likely stale, or the failure comes from data, time, or the platform.
- A file edited after the run is left out, on a `changed since the run, left out:` line, because its line numbers no longer match. Run the tests again.
- Without a trace, it prints `no trace of these tests; run them with pytest --xpt-trace to see the changed code they ran`. `pytest --lf --xpt-trace` reruns only the failed tests.
- Without git, or outside a repository, it prints one `changed code: unknown, ...` line.
- 10 lines of 80 characters are shown. `--full` lifts the limits.

### `xpt accept [ID ...]`

Write actual values into the test source.

| Option | Meaning |
|---|---|
| `--all` | Every acceptable call site that was not rejected. |
| `--missing` | Only empty expectations. |
| `--dry-run` | Report what would change and write nothing. |
| `--force` | Also accept values that look broken. See below. |
| `--verify` | After writing, rerun only the tests that observed the accepted call sites, and report whether they pass. |

After each call site's line, accept prints the changed code its tests ran, as [`xpt show`](#changed-code) does, read before anything is written. A call site with no trace prints nothing extra.

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

### `xpt env [NAME]`

Report the interpreter running xpt and what is wrong with the environment around it: modules that shadow the standard library or an installed package, and installed versions that drift from the lockfile. It prints one line for the interpreter and one per problem, and exits `1` when there is a problem. A clean environment prints one line:

```text
python 3.12.7 at .venv/bin/python (venv: .venv, matches uv.lock)
```

```text
python 3.12.7 at .venv/bin/python (venv: .venv, differs from uv.lock)
shadowing: json.py shadows the standard library module json
mismatch: requests 2.31.0 installed, uv.lock wants 2.32.3
missing: idna is not installed, uv.lock wants 3.7
```

The root is the nearest directory up from `--root` that holds `pyproject.toml` or `.git`. Paths inside it are relative, others absolute. It reads files and package metadata, never imports a project module, and writes nothing.

| Line | Means |
|---|---|
| `interpreter:` | `VIRTUAL_ENV` names another environment than the one running xpt, the project has a `.venv` that xpt is not running in, or `.python-version` asks for another Python. |
| `shadowing:` | A `name.py` or a `name/__init__.py` in the root, or in `sys.path[0]` when that is another directory outside the interpreter, has the name of a standard library module or of a top-level module of an installed distribution. The project's own distribution, named in `pyproject.toml` or installed editable from the root, does not count. |
| `mismatch:` | An installed version differs from the locked one. `1.0` and `1.0.0` are equal. |
| `missing:` | A package every environment needs is not installed. |
| `lockfile:` | A lockfile could not be read. |

The lockfiles are `uv.lock`, `poetry.lock`, and every `requirements*.txt` in the root. Each locked package that is installed is compared. Whether one that is not installed is missing depends on the file, because a lock also lists packages for other platforms, extras, and groups:

- `uv.lock`: missing when it is reachable from the project's own package through dependencies with no marker, following extras. Dependency groups such as `dev` are not followed.
- `poetry.lock`: missing when it is in the `main` group, not optional, and has no markers.
- `requirements*.txt`: each `name==version` line without an environment marker. Other lines are ignored.

`xpt env NAME` locates the module `NAME` as `import NAME` would with the root first on `sys.path`, as `python -m` and `python -c` run from the root have it. It finds the module without importing it or its parent packages, and prints where it comes from and the distribution that installed it:

```text
requests 2.32.3 from .venv/lib/python3.12/site-packages/requests/__init__.py
yaml (PyYAML 6.0.2) from .venv/lib/python3.12/site-packages/yaml/__init__.py
json from /usr/lib/python3.12/json/__init__.py (standard library)
```

It exits `1` when the module is not found, or when it would come from a project file that shadows the standard library or an installed package, which it reports on a second `shadowing:` line.

### `xpt api TARGET`

List compact signatures for the installed version of a package, module, class, function, or method, so an agent can check an API before it calls it. `TARGET` is a dotted name: `httpx`, `json.dumps`, `pathlib.Path`, or `argparse.ArgumentParser.add_argument`.

```text
$ xpt api json --grep decode
json stdlib (Python 3.11.18)
JSONDecoder(*, object_hook=None, parse_float=None, parse_int=None, parse_constant=None, ...)  Simple JSON <https://json.org> decoder
  .decode(s, _w=WHITESPACE.match)  Return the Python representation of `s` (a `str` instance containing...
  .raw_decode(s, idx=0)  Decode a JSON document from `s` (a `str` beginning with a JSON...
JSONDecodeError(msg, doc, pos)  Subclass of ValueError with the following additional properties:
```

The first line is the package and its installed version, from `importlib.metadata`. Then one line per public name: its signature and the first sentence of its docstring. A class's line shows its constructor, and its public methods and properties follow, indented and starting with `.`, including those it inherits from classes in the same package. An overloaded function has a line per overload. A module listed as a name says `module` and its dotted name.

- **Public names** are the module's `__all__`, or else the names without a leading underscore that the module defines or re-exports from its own package. A name explicitly targeted, such as `json._default_decoder`, is shown even when private.
- **Short form.** Past 80 characters a signature drops its optional parameters, leaving one `...`, and the annotations of its required parameters, which it always keeps. A default longer than 20 characters prints as `...`. Annotations lose `typing.` prefixes, and long subscripts fold to `[...]`. `--json` keeps everything.
- **No code runs by default.** xpt finds the module's files with the import system's finders, without importing it, and reads them with `ast`. A `.pyi` stub next to the source, or in a `<package>-stubs` package, is read instead of the source. Branches on `sys.version_info` and `sys.platform` follow the running Python.
- **What the source can't show** is listed as `unresolved` with the reason, and a last line counts them: a name bound at import time, such as one added through `globals()`, or one re-exported from an extension module with no stub. A module with no Python source, such as `math`, prints only a note. `--import` imports the module and reads those names with `inspect.signature`, which runs the module's code.
- **The environment is the interpreter's.** xpt reads the packages installed for the Python that runs it. Run it as `uv run xpt api` or `python -m xpt api` from the project's environment, not from a tool environment.

| Option | Meaning |
|---|---|
| `--depth N` | Also list submodules, `N` levels deep. Each name is listed once, under the first module that exposes it. |
| `--grep WORD` | Only names that contain `WORD`, ignoring case. A class is kept with only its matching members when its own name doesn't match. |
| `--limit N` | List at most `N` names, counting each member. Default 80, `0` for all. The output ends with how many were left out. |
| `--import` | Import the module and read what its source and stubs can't show with `inspect`. This runs the module's code. |

### `xpt coverage`

```text
xpt coverage [--since REF] [--full] -- PYTEST [ARG ...]
xpt coverage [--since REF] [--full] --from-coverage [PATH]
```

Run a pytest command and list the changed lines no test ran, grouped by function, with the source of each:

```text
$ xpt coverage -- pytest
src/app/load.py read_rows: 2 of 9 changed lines not run
  31 |         if row.get("count") is None:
  32 |             raise ValueError("missing count")
2 of 9 changed lines not run, in 1 function (since main).
```

- The changed lines are `git diff` against `--since REF`, with the same default and the same staged, unstaged, and untracked `.py` files as [Changed code](#changed-code).
- Only lines that hold code count, so blank lines, comments, and `else:` never show. When no changed line holds code, it says `No changed lines with code (since main).` and runs nothing.
- The command runs from the root, with its output on stderr. Give the whole pytest command, such as `xpt coverage -- uv run pytest -n 4`. The xpt plugin traces every project line the session runs, in every xdist worker, through `XPT_COVERAGE`. It needs no recording under `.xpt/`, but the plugin must load, so `PYTEST_DISABLE_PLUGIN_AUTOLOAD` must be unset. A command that traces no lines is a usage error.
- When every changed line ran, it prints `All changed lines ran: 9 lines (since main).`
- When pytest exits nonzero, a line says so, and the exit code is `1`, because a failing test may stop before lines it would have run.
- Lines run by other processes the tests start are not counted. Branches that were never taken are not reported yet.
- On Python 3.11 tracing uses `sys.settrace`, so it can't run beside coverage.py or a debugger, and it slows in-process tests down. On 3.12 and later it costs one callback per line for the session.

| Option | Meaning |
|---|---|
| `--from-coverage [PATH]` | Read the lines that ran from coverage.py's data file, `.coverage` under the root by default, instead of running a command. coverage.py need not be installed to read it. A changed file the data does not cover is named on a `not in the coverage data, left out:` line, and one edited after the data was written on a `changed since the coverage data, left out:` line. |
| `--full` | Lift the limits of 10 lines of 80 characters per function. |

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

### `xpt probe FILE:LINE [options] -- COMMAND [ARG ...]`

Run a command and record the values at one line of a Python file in every Python process it starts, without editing the source. See [Probing a line](probe.md). After the command finishes, it prints on stderr how often the line was hit, the locals of the first hit, and for each later hit the paths that changed. The exit code is the command's, 127 when it can't be started, and 2 when the file is missing or the line can never run. `--root` does not apply.

| Option | Meaning |
|---|---|
| `--expr EXPR` | Record this expression, evaluated in the frame, instead of the locals. |
| `--when COND` | Record only the hits where this condition is true. `FILE:LINE if COND` is the same. |
| `--max-hits N` | Record the first `N` hits in each process. Default 100. Later hits are still counted. |
| `--json` | Print one JSON object on stdout, and send the command's stdout to stderr. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The command did what it was asked, or there was nothing to do. |
| `1` | `failures` found failures, `flaky` found flaky call sites, `coverage` found changed lines no test ran or pytest failed, `accept` or `reject` left a requested call site stale, skipped, or suspect, the tests that `accept --verify` reran failed, an `init` check found a problem, or `env` found a problem or no module `NAME`. |
| `2` | A usage error, such as `xpt run` without a command, an `xpt probe` line that can never run, an unknown id or run, no recorded run, a malformed record, a held `.xpt/accept.lock`, a `convert` path that is missing or outside the root, an `api` target that is not installed or not found, unpaired markers in `AGENTS.md`, a malformed `.mcp.json` with `init --mcp`, or `coverage` outside a git repository, with a command that traced no lines, or with a file that is not coverage.py data. The error is one line on stderr that says what to do next, and nothing is written. |

## Files

```text
.xpt/
  .gitignore            "*", written on first use
  runs/<run>/<worker>.jsonl
  runs/<run>/source     the project source digest, for xpt flaky
  runs/<run>/<worker>.trace.json  with pytest --xpt-trace, the lines each failing test ran
  latest                the id of the newest complete run
  probes/<id>.jsonl     the hits of one xpt probe, the last five kept
  decisions.jsonl       rejections, one per line
  accept.lock           held while accept or reject writes
```

Each pytest process, the controller and each xdist worker, writes its own file, so nothing is shared during a run. The last five runs are kept.
