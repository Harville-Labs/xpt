# xpt design

Status: draft for review. Package and CLI name `xpt`. `expect`, `expectly`, `pyexpect`, and `expt` are taken on PyPI.

xpt is an inline expect-testing library for Python. A test calls `expect(actual)` or `expect(actual, expected)`. When the value is missing or different, the run records what it saw. A separate command shows the difference and writes an accepted value into the test source, without running the tests again.

It is built for coding agents and must stay just as usable by people. It needs no model, network service, or agent framework.

The guiding question for every decision: does this make writing, running, understanding, or maintaining tests cheaper for a coding agent, without costing correctness or usability for a human?

## Prior art and where we differ

We read the source of all four projects (default branch, November 2026). File references are in `docs/research.md`.

| Capability | expecttest | inline-snapshot | syrupy | ApprovalTests | xpt |
|---|---|---|---|---|---|
| Inline expectations in test source | Strings only | Yes, any value | No | Docstrings only | Yes, any supported value |
| Native values, not strings | No | Yes | Yes (external) | JSON or text | Yes |
| Create missing values | Accept mode | `create` | `--snapshot-update` | Approve file | `accept --missing` |
| Accept later without a rerun | No | No | No | Yes, by moving files | Yes, per change |
| Review per change | No | No, one prompt per category | No | Per file, in a diff tool | Yes |
| Persisted proposed changes | No | No | No | `.received` files | `.xpt/runs/` |
| Stale-source detection | No | No | No | No | Yes |
| Structured path diff | No | No | No | No | Yes |
| JSON output | No | No | No | No | Yes |
| pytest-xdist | Edits from each worker | Disabled under xdist | Locks files | Not handled | Workers record, no edits |
| unittest | Yes | No | No | Yes | Yes |
| Runtime dependencies | None | 5 (executing, asttokens, rich, pytest, typing-extensions) | pytest | pytest plus 2, and extras installed by default | None |
| Source rewriting | Regex over a line range | Offset edits through asttokens, then optional black | n/a | Rewrites the whole file | Offset edits through stdlib `ast` |

What is not new, and we should say so:

- Inline values that are real Python values, rewritten in place with formatting kept. inline-snapshot does this well.
- Finding the running call precisely. inline-snapshot keys on `(code object, f_lasti)` and uses `executing`. We use the same idea through stdlib `co_positions`.
- Approving later without a rerun. ApprovalTests leaves `.received` files on disk and writes an approval script.
- Pluggable serializers, sorted output, cycle limits, and path matchers. syrupy has them.
- Detecting conflicting values from one call site. expecttest does it within one process.

What we found in none of them, which is where xpt differs:

1. One observation per assertion, persisted across processes, with list, show, accept, and reject for each change.
2. Stale-source detection before any write.
3. Path-based structured diffs with counts of unchanged elements.
4. A JSON interface with a schema version for every operation.
5. xdist that works, because workers never write source.
6. No runtime dependencies.

### Why not contribute to inline-snapshot instead

We considered this seriously, because inline-snapshot is mature and well designed. Two of its foundations work against our core feature:

- It builds new source in `pytest_sessionfinish` from live Python objects, with access to the test's local and global variables (`customize(value, builder, local_vars, global_vars, ...)`). Accepting later in another process needs a serialized value, and its code generation is built around the live object.
- It is pytest-only and turns itself off under xdist by design.

Changing either would be a rewrite of its core, not a pull request. We should still open a discussion upstream before M2, describing the record-then-accept model. If the maintainer wants it, contributing is better for users than a new library.

## Decisions that change the original brief

The brief was a good starting point. These parts of it change.

1. **Recording is on by default. There is no `--expect-record` flag.** The point is to avoid a second test run. If an agent must rerun with `--expect-record` after a plain `pytest` fails, the flag costs the very run we are trying to save. Every run writes its failed and missing observations to `.xpt/`, which is gitignored. `--expect-no-record` turns this off. The cost is a few kilobytes per failing assertion. The privacy cost is no worse than pytest's own failure output, which already prints the same values. `xpt clean` deletes the directory.
2. **`pytest --expect-create` does not edit files during the run.** It runs the tests, then applies the recorded `missing` observations through the same code path as `xpt accept --missing`, after the session ends. One rewrite path means one set of safety checks. Test execution and source edits stay separate, as the brief asks. A mismatch is never accepted by this flag. Under xdist only the controller writes, after every worker has finished. The exit status is still pytest's, so a run that fills expectations fails, because those tests failed when they ran. `--expect-create` with `--expect-no-record` or `XPT_NO_RECORD` is a usage error, because there would be nothing to fill.
3. **No LibCST.** The prototype in `docs/prototypes/` shows that the standard library is enough. `code.co_positions()` (Python 3.11+) gives the exact span of the running call. `ast` gives the span of each argument. A byte-offset splice replaces only the expected argument, so comments, formatting, and every other line stay untouched. LibCST would add a native wheel and a YAML dependency to rewrite one argument we can already locate exactly. We still parse the result with `compile()` before writing.
4. **No runtime dependencies.** pytest is optional. `executing` and `asttokens`, which inline-snapshot uses, are not needed on Python 3.11+.
5. **Python 3.11 is the floor.** 3.10 reached end of life in October 2026, and 3.11 brings `co_positions` and `co_qualname`.
6. **JSON Lines files, not SQLite.** Each process writes its own file, so there is no shared write target and no lock (see Persistence).
7. **The CLI is `xpt`, with `python -m xpt` as a fallback.** A command named `expect` would collide with the Tcl `expect` tool that many systems install.
8. **Benchmarking agent efficiency is its own project phase, not an MVP requirement.** It is the claim the project lives or dies on, so it gets a real harness (milestone M5) instead of a spreadsheet of guesses.

## Data shapes

Everything else follows from these records.

```python
CallSite        # where an expect() call lives in source
  path: str               # relative to the project root
  qualname: str           # "TestParser.test_add", from co_qualname
  callee: str             # the called function's source text: "expect", or a helper's name
  ordinal: int            # index of this call among calls to the same callee in that function
  arg_sha: str            # sha256 of the expected-argument source text, or "" when absent
  line: int               # for display only, never used to locate the call

Unlocatable     # a call xpt cannot rewrite (no source file, file changed since import)
  reason: str
  path: str
  line: int

Value           # a serialized, typed value tree. Never code, never pickle.
  "a" | 1 | 1.5 | true | null | [Value, ...]     # plain JSON where JSON keeps the type
  {"tuple" | "set" | "frozenset": [Value, ...]}
  {"float": "nan" | "inf" | "-inf"}
  {"dict": {str: Value, ...}}                      # every key is a str
  {"dict": [[Value, Value], ...]}                  # any other keys
  {"module:Qualname": {field: Value, ...}}         # a dataclass
  {"adapter": {"name": "numpy.ndarray", "data": Value}}

Observation     # one failed call of expect() in one run
  run: str                # run id
  test: str               # pytest node id, or the expect() caller's qualname outside pytest
  site: CallSite | Unlocatable  # an unlocatable site is shown but never accepted
  status: "missing" | "mismatch" | "unsupported"
  expected: Value | None  # None when missing or unsupported
  actual: Value | None    # None when unsupported or over the size cap
  detail: str             # "" normally; the UnsupportedValue message, or "too large: N bytes, sha256 <hex>"

Group           # every failed observation of one call site in a run, built at read time
  id: str | None          # short display id, None for an unlocatable site
  site: CallSite | Unlocatable
  status: "missing" | "mismatch" | "unsupported" | "unlocatable" | "conflict"
  actuals: [Value]        # the distinct actual values, one when the calls agree
  expected: Value | None  # None when missing, or when the calls disagree on it
  tests: [str]            # every test that reached the site
  detail: str             # why the group cannot be accepted, "" when it can
  acceptable              # derived: status is missing or mismatch

Outcome         # what accept did with one group
  group: Group
  status: "accepted" | "already_accepted" | "stale" | "skipped"
  reason: str             # why it was not written, "" when accepted or already accepted

AcceptResult
  outcomes: [Outcome]     # one per group, in the order given
  changed: [str]          # project-relative paths that were rewritten

Change
  path: [str | int]       # ["expression", "operator"]
  op: "replace" | "add" | "remove"
  expected: Value | None
  actual: Value | None
```

An observation stores no diff and no id. Readers derive both: `diff(expected, actual)` gives the changes, and `site_id(site)` gives the id. One copy of each rule means a stored diff can never disagree with the values it came from. `conflict` is not recorded either. It is the status of a group of observations whose values differ (see Conflicting observations).

A passing assertion writes nothing. Run state lives on disk as records. In memory there is only the open file handle.

## Identity and stale detection

An expectation's identity is its `CallSite` minus `arg_sha` and `line`: `(path, qualname, callee, ordinal)`. The callee is part of it because a helper called with `_depth=1` and a direct `expect()` can share an ordinal in one function. The display id is the first 4 hex characters of `sha256(path, qualname, callee, ordinal)`, lengthened like a git short hash when two ids collide. The id stays the same across runs as long as the call stays in the same function at the same position among its sibling `expect()` calls. Line numbers can change freely.

`arg_sha` is the fingerprint. At accept time we:

1. Parse the file as it is now.
2. Find the function by `qualname` and the call by `callee` and `ordinal`.
3. Hash the expected argument's current source text and compare it to `arg_sha`.
4. Refuse if the function, the call, or the hash does not match. The group is `stale`, and its reason names the file and the fix: `tests/test_parser.py:12 changed since the run. Run the tests again.`

Before the hash check, accept renders the call it would write. If the file already holds exactly that text, the group is `already_accepted`. This is what makes a second accept a no-op instead of a stale refusal.

This refuses exactly when it should. Editing an unrelated test, adding a line above, or reformatting a different call does not invalidate the record. Editing the expectation itself, or adding an `expect()` call above it in the same function, does. The second case is a false refusal we accept, because it is cheap to fix and a false accept is not.

We do not hash the whole file. A whole-file hash would refuse every accept after any edit, and agents edit tests constantly.

## Conflicting observations

One call site can run many times: in a loop, in a parametrized test, from a helper. The records for one site are grouped at read time.

- If every observation has the same value, it is a single change.
- If the values differ, the status is `conflict`. Accept refuses and lists the distinct values with their test ids. The fix is a separate `expect()` per case, or `expect(value, expected, key=param)` in a later milestone.
- Values are equal when `norm` says so, the same equality `diff` uses, so `1` and `1.0` agree.
- Calls that disagree on `arg_sha` are a conflict too, because the file changed during the run. So are calls with equal actual values but different expected values, which happens when the expected argument depends on a parameter.
- If any call recorded no actual value (unsupported, or over the size cap), the group is `unsupported` and cannot be accepted.
- Unlocatable observations group by path, line, and reason. They are shown with status `unlocatable` and never accepted.

`xpt.group.group(observations)` does this. It is pure, and its result is sorted by path, line, and ordinal, so the input order does not matter.

expecttest detects the same case within one process. We detect it across processes and across xdist workers, because the grouping happens after recording.

## Lifecycle

```
pytest run
  expect(actual, expected?)
    compare -> pass: nothing written
            -> fail: Observation appended to .xpt/runs/<run>/<worker>.jsonl
  session end: with --expect-create, accept the run's missing groups (as xpt accept --missing)
               one block per failure, a one-line footer with the next command, pytest's exit code

xpt failures                every group of the latest run, each with its diff
xpt show <id>               one group with its function and tests, paths only, with limits
xpt show <id> --full        no limits, plus both whole values
xpt accept <id>... [--dry-run]
xpt accept --missing        fill every missing expectation, nothing else
xpt accept --all            every acceptable group; the rest are reported with their reason
xpt runs                    the recorded runs, newest first
xpt clean                   delete .xpt/
xpt reject <id>...          planned in #30
```

Every command takes `--json`, and `--root PATH` for a project other than the current directory. `failures`, `show`, and `accept` take `--run ID` to read an older run instead of the latest. `python -m xpt` runs the same command.

Exit codes:

- `0`: the command did what it was asked, or there is nothing to do.
- `1`: `failures` found failures, or `accept` left a requested group stale or skipped.
- `2`: a usage error, an unknown id or run, no `.xpt/latest`, a malformed record, or a held `.xpt/accept.lock`. The error is one line on stderr that says what to do next, and nothing is written.

Accept is a state machine per observation group: `recorded -> accepted | rejected | stale`. A group moves to `stale` when its fingerprint check fails. Accept is idempotent. Running it twice leaves the file as it was after the first run, because the second run finds the fingerprint already matches the accepted value and has nothing to do.

`accept` without arguments does nothing and says so. Replacing existing expectations needs ids or `--all`. `--missing` fills only empty expectations, because creating one does not overwrite a reviewed value. The design first named the bulk flag `--all-mismatches` so that typing it would be deliberate. `--all` is just as explicit, also fills missing values, and is the flag agents reach for. `xpt failures` prints every diff before the footer suggests an accept, so the change is on screen when it is accepted.

## Persistence

```
.xpt/
  .gitignore            # "*", written on first use
  runs/<run>/<worker>.jsonl
  latest                # the run id of the newest complete run
  decisions.jsonl       # accept and reject events
```

- Each pytest process, controller or xdist worker, appends to its own file. Nothing is shared, so nothing is locked. This follows "separate before serializing shared state."
- The controller writes `latest` at session end with `os.replace`, so a crashed run never becomes the latest.
- Old runs are pruned to the last 5 by default.
- Each line is one JSON object with `"schema_version": 1`. A value that is absent (`expected` when missing) is left out of the object, so a recorded `None` stays distinct from no value. Reading a run validates every line and names the file and line of a malformed one.
- A process with no driver, such as plain unittest or a script, records under the current directory and writes `latest` from an `atexit` hook. Under xdist, the controller hands its run id to each worker.
- A record over 1 MB keeps its status and site but drops both values. Its detail holds the record's size and the sha256 of the value.
- `XPT_NO_RECORD` set to any non-empty value turns recording off, like `--expect-no-record`.
- `XPT_REPORT` set to `pytest` keeps pytest's terminal output, like `--xpt-report=pytest`.
- If a record cannot be written, the test still fails with its own message, plus one line naming the I/O error.
- Source writes go to a temp file in the same directory, then `os.replace`, keeping the file's mode. Every accepted change to one file is applied in a single parse and write, last offset first. The new text must compile before it is written.

## Writing the expected value

- A missing expectation becomes the second positional argument. A mismatch replaces the expected argument where it is, positional or `expected=`. Other arguments, such as `_depth=1`, stay where they are.
- The whole call is reprinted the way `ruff format` would at line width 100. It stays on one line if it fits, then splits to one indented line of arguments, then one argument per line with a trailing comma. A magic trailing comma or a multi-line string forces the last form.
- A call with a comment, `*args`, or `**kwargs` inside it is not reprinted. Only the expected argument is written, so the comment survives. The result is valid Python but may need a `ruff format`.
- The rest of the file keeps its bytes. Offsets are UTF-8 byte offsets, so non-ASCII text before a call does not shift the edit. The file keeps its newline style and its byte order mark.
- Files that are not UTF-8, by declaration or by content, are refused.
- A dataclass value is written by its class name only when the file binds that name at module level, by import, class, or assignment. Otherwise the group is skipped and the reason says which import is missing.
- Groups that are not acceptable are skipped with their `detail`, without reading the file.

SQLite would buy queries we do not need. It would also make concurrent writers our problem. JSON Lines is readable with `jq` and easy to delete.

## Serialization, comparison, and source generation

These are separate functions over the `Value` tree:

- `serialize(obj) -> Value` turns a Python object into a tree. It raises `UnsupportedValue` with the path and type for anything it cannot represent: `result["handler"] is a function; xpt cannot record it. Register an adapter or expect a field of it instead.` It detects cycles by `id()`.
- `diff(expected, actual) -> [Change]` walks two trees. Dicts compare by key, sequences by index, sets as sorted members. The walk is pure and easy to test exhaustively.
- `to_source(Value, indent, width) -> str` prints a tree as a Python literal. Dict order is kept as observed. Set members are sorted. Floats use `repr`, which round-trips. It fits on one line when possible, and otherwise uses one element per line with a trailing comma, so the output already matches ruff's format.

Comparison is Python `==` on the reconstructed expected value, with `diff` used only to explain a failure. That keeps xpt's results identical to what a human writing `assert actual == expected` would get.

A dataclass prints as `Point(x=1, y=2)`. At record time we check that `Point` resolves to the same class in the test module's globals. If it does not, we mark the value unsupported instead of writing code that will raise `NameError`.

Adapters are a small registry:

```python
@register(numpy.ndarray)
class NDArray:
    def serialize(self, value) -> Value: ...
    def to_source(self, value: Value, ctx) -> str: ...
    def diff(self, expected, actual, *, atol, rtol) -> list[Change]: ...
```

There is no `supports()` method and no `compare()`. The registry looks up types through the MRO. Comparison stays `==`, unless the adapter defines tolerance-aware `diff`. The adapters for NumPy, pandas, PyTorch, and Pydantic import lazily and live behind extras.

Acceptance never runs code from the record. It builds source text from the `Value` tree. It never calls `eval`, `pickle`, or the observed object's `__repr__`.

## Reporting

When xpt is installed, it replaces pytest's terminal output. Agents read every byte of a test run, and pytest's header, progress dots, rules, and full tracebacks spend tokens without saying more. The report stays readable for people, so it is the default. `--xpt-report=pytest`, or `XPT_REPORT=pytest`, keeps pytest's own output.

```
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
FAILED tests/test_math.py::test_double[0]  tests/test_math.py:22
  also fails: [1] [3] [4]
  > assert double(x) == x + 2
  AssertionError: assert (0 * 2) == (0 + 2)
  stdout: debug line 0
7 failed, 2 passed in 0.31s. Next: xpt accept a81f
```

- Each failure is one block. The header names the test and the deepest frame in a project file, outside `site-packages` and pytest. ` in <function>` follows when that frame is not the test function. When the error is raised in a helper, the header shows the chain of project frames from the test down instead, as `path:line > path:line function`, keeping the first frame and the three nearest the error. The test's own name is left out because the node id has it. The next line is the source line at the deepest frame, after `> `. Setup and teardown errors say `ERROR` and name the phase. Collection errors say `ERROR <path> (collect)` and keep pytest's `E` lines.
- An `expect()` failure shows its short id, its status, and the path diff.
- A plain `assert left == right` on values xpt can serialize gets the same path diff, from `pytest_assertrepr_compare`, with each side labeled: `left 41, right 42`. It has no arrow, because `expected -> actual` would read backwards for `assert actual == expected`. Scalars, unsupported values, and other operators keep pytest's explanation.
- Any other exception is one line, `Type: message`, cut to 200 characters. An exception raised from another, with `raise X from Y` or inside an `except` block, adds `caused by: Type: message`. `raise X from None` adds nothing.
- Parametrized cases that fail at the same line with the same kind of error fold into the first case, and `also fails:` lists the rest.
- A failing test prints the last 5 lines of its captured stdout, stderr, and log.
- The footer is pytest's counts, warnings included, and duration, plus `Next: xpt accept <ids>` when recorded expect failures can be accepted, or `Next: xpt failures` when none can. With `--expect-create`, it first says `Filled N missing expectations: path:line ...`, and `Next:` covers only what was not written. When every expect failure was written, it says `Next: run the tests.` With `--xpt-report=pytest`, the same `Filled` line prints after pytest's summary.
- `-v` raises the limits to 50 changes, 400 characters per value, 20 output lines, and 1000 characters per message. `-vv` lifts them and adds pytest's traceback. `-q` prints the same compact report.
- Internal errors, `-x` stops, and other plugins' summary lines still print. Exit codes are pytest's. Under xdist the controller prints one report, and worker status lines are dropped.
- `--xpt-json PATH` also writes the report as one JSON object with `"schema_version": 1`, `exit_code`, `summary`, `counts`, `duration`, `run`, `created`, and `failures`. `created` is null without `--expect-create`. With it, `created` holds `outcomes` and `changed` in the same form as `xpt accept --json`. A held `.xpt/accept.lock` skips every missing group with the lock's message instead of failing the session. Each failure has its test, phase, location, `callers`, `source`, `cause`, `detail`, captured `output`, and full `traceback`, with no limits. An expect failure adds its `group` in the same form as `xpt failures --json`, and a compared assert adds `changes` and `unchanged`.

The expect limits are 10 changes per expectation and 80 characters per value, cut with a length marker. `xpt show --full` lifts both.

`xpt failures` prints the same block per group, a one-line footer with the counts, and the next command:

```
a81f  tests/test_parser.py:14  mismatch  2 tests
  value.operator: "addition" -> "subtraction"
  1 changed, 42 unchanged
1 failure in run 20260101T120000-ab12 (1 mismatch), 1 acceptable. Next: xpt accept a81f
```

There is no color and no banner. `--json` prints one JSON object on stdout with `"schema_version": 1` and `"command"`. Values use the recorded JSON form, and `expected` is left out when there is none, so a recorded `None` stays distinct. A mismatch adds `changes` (path, op, expected, actual) and `unchanged`. Accept adds `outcomes` and `changed`.

The CLI handlers are thin wrappers over `xpt.api`, which re-exports `load_run`, `group`, `accept`, `diff`, and their data types, so an MCP server later is another thin wrapper and not a redesign.

## Concurrency

- xdist: workers record and never write source. The controller sees every worker's file at read time. No IPC is needed. `--expect-create` runs in the controller's `pytest_sessionfinish`, after every worker has finished.
- Two pytest sessions at once in one project each get their own run directory. `latest` names whichever finished last. That is acceptable, and `--run <id>` selects another.
- Accept while pytest runs: accept creates `.xpt/accept.lock` with `O_CREAT | O_EXCL`, which behaves the same on POSIX and Windows, and deletes it when done. A second accept fails with a message naming the lock file. A crash can leave the file behind, so the message says to delete it if no accept is running. A test run racing an accept is caught by the fingerprint check on the next accept.

## Risks

- **Rubber-stamping.** The tool makes accepting cheap, so agents will accept regressions. Mitigations: no bulk accept by default, the diff shown at accept time, and `--dry-run`. The benchmark (M5) measures how often wrong accepts happen. We do not claim to prevent them.
- **Dynamic call sites.** `expect` called through a helper reports the helper's frame. We support `expect(..., _depth=1)` the way expecttest supports `skip`. A call created through `functools.partial` or `exec` has no source to rewrite. We record it as unsupported for rewriting, and the comparison still runs.
- **Generated or non-file sources** (doctests, notebooks, `python -c`): same as above.
- **Floats and nondeterminism.** Dict order, set order, timestamps, ids, and memory addresses make snapshots flaky. Sorting covers sets. `ignore=` and adapters cover the rest. Default `repr` of arbitrary objects is never used, because it embeds addresses.
- **Large values.** Records are capped at 1 MB per observation by default. Larger values are recorded as a hash plus a summary and cannot be inline-accepted.
- **Sensitive data.** `.xpt/` holds real values from test runs. It is gitignored on creation, pruned to 5 runs, and removed by `xpt clean`. The README says so plainly.
- **Python 3.11 floor** excludes 3.10 users. 3.10 is end of life, so this is acceptable.
- **`co_positions` can be disabled** with `-X no_debug_ranges`. We detect `None` positions and fall back to line-only matching, refusing when a line holds more than one call.

## Minimum public API

```python
from xpt import expect

expect(actual)  # missing: fails, records
expect(actual, expected)  # compares with ==, records on mismatch
```

Later, as keyword-only arguments: `ignore=`, `unordered=`, `atol=`, `rtol=`, `key=`. These stay in the source, so a reviewer can see them.

pytest plugin options: `--expect-create`, `--expect-no-record`, `--xpt-report=xpt|pytest`, and `--xpt-json PATH`. unittest works with no plugin, because `expect` raises `AssertionError` and records through the same process-level recorder. Its test id falls back to the qualname.

## Roadmap

Each milestone ships on its own and is tracked as GitHub issues.

- **M0. Scaffold.** uv, ruff, ty, pytest, CI on 3.11 to 3.14, MIT license.
- **M1. Core assertion.** `Value`, `serialize`, `diff`, `to_source` for builtins and dataclasses. `expect()` compares and raises with the compact message. No recording yet.
- **M2. Record and accept.** Call-site identity, JSON Lines recorder, `failures` / `show` / `accept` / `reject` / `clean`, fingerprint checks, atomic multi-edit writes, `--expect-create`. This is the differentiator and the MVP line.
- **M3. Reporting polish and comparison options.** pytest longrepr replacement, limits, `ignore=`, `unordered=`, `key=` for parametrized tests.
- **M4. Adapters and xdist.** NumPy, pandas, PyTorch, Pydantic extras with tolerances. An xdist test matrix.
- **M5. Agent benchmark.** Fixture repos with seeded regressions. Claude Code and Codex run headless against plain asserts, expecttest, inline-snapshot, and xpt. We measure tokens, tool calls, test runs, wall time, wrong accepts, and final correctness.
- **M6. Release.** Name check, docs, PyPI.
