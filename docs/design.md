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
2. **`pytest --expect-create` does not edit files during the run.** It runs the tests, then applies the recorded `missing` observations through the same code path as `xpt accept --missing`, after the session ends. One rewrite path means one set of safety checks. Test execution and source edits stay separate, as the brief asks. A mismatch is never accepted by this flag. Under xdist only the controller writes, after every worker has finished. The exit status is still pytest's, so a run that fills expectations fails, because those tests failed when they ran. `--expect-create` with `--expect-no-record` or `XPT_NO_RECORD` is a usage error, because there would be nothing to fill. `pytest --expect-accept=ID[,ID...]` follows the same rule for named call sites, through the same path as `xpt accept <id>`. It exists because agents that read only pytest output had to learn a second tool before they could accept, and some read `xpt --help` first. An id that names no group in the run is reported as `unknown` and the others are still written, so one stale id does not block the rest. A named id that was rejected is still accepted, as on the CLI.
3. **No LibCST.** The prototype in `docs/prototypes/` shows that the standard library is enough. `code.co_positions()` (Python 3.11+) gives the exact span of the running call. `ast` gives the span of each argument. A byte-offset splice replaces only the expected argument, so comments, formatting, and every other line stay untouched. LibCST would add a native wheel and a YAML dependency to rewrite one argument we can already locate exactly. We still parse the result with `compile()` before writing.
4. **No runtime dependencies.** pytest is optional. `executing` and `asttokens`, which inline-snapshot uses, are not needed on Python 3.11+.
5. **Python 3.11 is the floor.** 3.10 reached end of life in October 2026, and 3.11 brings `co_positions` and `co_qualname`.
6. **JSON Lines files, not SQLite.** Each process writes its own file, so there is no shared write target and no lock (see Persistence).
7. **The CLI is `xpt`, with `python -m xpt` as a fallback.** A command named `expect` would collide with the Tcl `expect` tool that many systems install.
8. **Benchmarking agent efficiency is its own project phase, not an MVP requirement.** It is the claim the project lives or dies on, so it gets a real harness (milestone M5) instead of a spreadsheet of guesses.
9. **`xpt api` ships inside xpt, not as a separate package.** It is the furthest of the agent helpers from expect testing, but it serves the same reader and the same goal: fewer failed runs. A separate package would be one more thing to install and keep at the right version in every environment, and it needs nothing xpt doesn't already allow (stdlib only, no network). It reads source with `ast` by default and imports only under `--import` (see Installed API listing).

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
  {"module:Qualname": {field: Value, ...}}         # a constructor call; field "" is positional
                                                   # and field "*" holds positional args as a tuple
  {"ref": "module:qualname"}                       # a module-level name such as torch.float32
                                                   # or an enum member, "shop:Color.RED"
  {"datetime:datetime": {"year": 2000, ...}}       # standard-library values are constructor
  {"uuid:UUID": {"": "0000...0001"}}               # calls too (see Standard-library values)

Observation     # one failed call of expect() in one run
  run: str                # run id
  test: str               # pytest node id, or the expect() caller's qualname outside pytest
  site: CallSite | Unlocatable  # an unlocatable site is shown but never accepted
  status: "missing" | "mismatch" | "unsupported" | "returned"
  expected: Value | None  # None when missing or unsupported
  actual: Value | None    # None when unsupported or over the size cap; for returned, the return value
  detail: str             # "" normally; the UnsupportedValue message, "too large: N bytes, sha256 <hex>",
                          # or for returned, the failure message
  options: Options        # how the call compared; both values already have ignore and unordered applied
  key: Value | None       # the case's key= value, None for an unkeyed call

Options         # the comparison keywords of one expect() call
  tolerance: Tolerance    # atol and rtol
  ignore: [path]          # dict entries removed from both values, parsed from strings like 'value["id"]'
  unordered: bool         # lists and tuples sorted and compared as multisets

Group           # every failed observation of one call site in a run, built at read time
  id: str | None          # short display id, None for an unlocatable site
  site: CallSite | Unlocatable
  status: "missing" | "mismatch" | "unsupported" | "returned" | "unlocatable" | "conflict"
  actuals: [Value]        # the distinct actual values, one when the calls agree
  expected: Value | None  # None when missing, or when the calls disagree on it
  tests: [str]            # every test that reached the site
  detail: str             # why the group cannot be accepted, "" when it can
  rejected: bool          # its one actual value matches a Rejection in decisions.jsonl
  options: Options        # the first observation's, used for every diff of the group
  volatile: [Volatile]    # paths that changed between stored runs with the same source
  acceptable              # derived: status is missing or mismatch
  offered                 # derived: acceptable, not rejected, not volatile; what footers suggest

Volatile        # a path of a group's value that differed between stored runs (see Flaky values)
  path: [str | int]       # ["created_at"]
  kind: "timestamp" | "value" | "order" | "float"
  values: int             # distinct values at the path across those runs
  runs: [str]             # the run ids compared, which share arg_sha and the source digest
  spread: float | None    # the largest difference, for floats
  suggestion              # derived: ignore=[...], unordered=True, atol=..., or None

Trace           # the project lines each recorded test ran, from pytest --xpt-trace (see Changed code)
  files: {path: str}      # sha256 of each file a stored test ran, when the run ended
  tests: {test: {path: [int]}}  # node id -> 1-based lines it ran, with the module and class
                                # body lines of each file it ran

Map             # the project lines every test ran, from pytest --xpt-map (see Affected tests)
  commit: str | None      # HEAD when the map was built, None without git
  files: {path: str}      # sha256, with \r\n as \n, of each file the run imported or ran
  tests: {test: {path: [int]}}  # node id -> 1-based lines it ran, without module bodies

Hunk            # one run of changed lines from git diff, against the working tree
  path: str
  start: int              # first new-side line; for a removal, the line before it
  count: int              # new-side lines, 0 for a removal
  removed: [str]
  added: [str]
  old_start: int          # first removed line in the base; for an addition, the line before it

ChangedCode     # what show and accept print under a group (see Changed code)
  status: "ran" | "none_ran" | "no_trace" | "no_git"
  since: str | None       # the ref diffed against, None without a diff
  functions: [(path, function | None, [Hunk])]  # changed hunks the group's tests ran
  stale: [path]           # files the tests ran that changed since the run, left out
  note: str               # the git error for no_git

Outcome         # what accept did with one group
  group: Group
  status: "accepted" | "already_accepted" | "stale" | "skipped" | "suspect"
  reason: str             # why it was not written, "" when accepted or already accepted

AcceptResult
  outcomes: [Outcome]     # one per group, in the order given
  changed: [str]          # project-relative paths that were rewritten

Rejection       # a call site's actual value rejected as a bug, one line of decisions.jsonl
  site: (path, qualname, callee, ordinal)  # the CallSite identity, as for the display id
  actual_sha: str         # sha256 of the actual value's canonical JSON

RejectOutcome   # what reject did with one group
  group: Group
  status: "rejected" | "already_rejected" | "skipped"
  reason: str             # why it was not recorded, "" otherwise

Change
  path: [str | int]       # ["expression", "operator"]
  op: "replace" | "add" | "remove"
  expected: Value | None
  actual: Value | None
```

An observation stores no diff and no id. Readers derive both: `options.diff(expected, actual)` gives the changes, and `site_id(site)` gives the id. One copy of each rule means a stored diff can never disagree with the values it came from. `conflict` is not recorded either. It is the status of a group of observations whose values differ (see Conflicting observations).

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
- If the values differ, the status is `conflict`. Accept refuses and lists the distinct values with their test ids. The fix is a separate `expect()` per case, or `expect(value, {param: expected, ...}, key=param)`.
- With `key=`, every observation stores the whole expected dict and, as its actual value, that dict with its own case replaced. Grouping merges them: the group's actual value is the expected dict with every failing case's entry set. Cases that passed record nothing, so their entries come through unchanged from the expected dict. The group is `missing` when every failing case had no entry, and `mismatch` otherwise. Two calls with the same key and different values conflict, and so does a site that mixes keyed and unkeyed calls.
- Values are equal when `norm` says so, the same equality `diff` uses, so `1` and `1.0` agree.
- Calls that disagree on `arg_sha` are a conflict too, because the file changed during the run. So are calls with equal actual values but different expected values, which happens when the expected argument depends on a parameter.
- If any call of `expect.raises` returned, or its block finished, instead of raising, the group is `returned` and cannot be accepted. Its detail holds each distinct failure message.
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
  with --xpt-trace: the project lines of each test that recorded one, in <worker>.trace.json
  with --xpt-map: the project lines of every test, joined into .xpt/map.json at session end
  session end: with --expect-accept, accept the named groups (as xpt accept <id>)
               with --expect-create, accept the run's other missing groups (as xpt accept --missing)
               one block per failure, a one-line footer with the next command, pytest's exit code

xpt failures                every group of the latest run, each with its diff
xpt show <id> [--since REF] one group with its function and tests, paths only, with limits,
                            and the changed code its tests ran (see Changed code)
xpt show <id> --full        no limits, plus both whole values
xpt accept <id>... [--dry-run] [--force] [--verify] [--since REF]
xpt accept --missing        fill every missing expectation, nothing else
xpt accept --all            every group not rejected; those that cannot be accepted are reported with their reason
xpt runs                    the recorded runs, newest first
xpt flaky                   call sites whose values changed between stored runs with the same source
xpt env [NAME]              the interpreter, shadowed modules, and lockfile drift; or where NAME imports from
xpt coverage [--since REF] -- pytest ...   changed lines no test ran, by function (see Coverage in the diff)
xpt coverage --from-coverage [PATH]        the same, from coverage.py's data file
xpt clean                   delete .xpt/
xpt reject <id>...          record each group's actual value as a bug, never to be offered
xpt affected [--since REF]  the tests that ran the changed code, each with its reason (see Affected tests)
xpt affected -- pytest ...  run them, and exit with pytest's code
```

Every command takes `--json`, and `--root PATH` for a project other than the current directory. `failures`, `show`, `accept`, and `reject` take `--run ID` to read an older run instead of the latest. `python -m xpt` runs the same command.

Exit codes:

- `0`: the command did what it was asked, or there is nothing to do.
- `1`: `failures` found failures, `flaky` found flaky call sites, `env` found a problem, `coverage` found changed lines no test ran or the tests failed, or `accept` or `reject` left a requested group stale, skipped, or suspect, or the tests that `accept --verify` reran failed.
- `2`: a usage error, an unknown id or run, no `.xpt/latest`, a malformed record or decision, or a held `.xpt/accept.lock`. The error is one line on stderr that says what to do next, and nothing is written.

Accept is a state machine per observation group: `recorded -> accepted | rejected | stale`. A group moves to `stale` when its fingerprint check fails. It moves to `rejected` when `xpt reject` records its actual value as a bug.

### Rejection

`xpt reject <id>...` is for an agent or a person who has read a diff and decided the actual value is a regression, not a new expectation. Without it, every later `xpt failures` and pytest footer suggests `xpt accept` for the same bug again, and an agent that follows the footer accepts it.

A rejection is one line in `.xpt/decisions.jsonl`:

```json
{"schema_version": 1, "decision": "reject", "run": "20260101T120000-ab12", "site": {"path": "tests/test_parser.py", "qualname": "test_parser", "callee": "expect", "ordinal": 0}, "actual_sha": "<64 hex>"}
```

- It names the call site by its identity, the same four fields the display id hashes, so it survives line moves like the id does. `run` records where the decision came from and is not used to match.
- It names the value by `actual_sha`, the sha256 of its canonical JSON (sorted keys, no spaces). It stores no value, so the file holds no test data and is never pruned.
- A group is `rejected` when it is acceptable, has one actual value, and some line matches its site and that value's hash. Conflicts, unsupported values, and unlocatable sites have no single value to reject, so `reject` reports them `skipped` with the group's detail.
- A rejection does not expire by time. It stops matching as soon as a run records a different actual value at that site, and that group is offered again. If the same wrong value comes back later, it is still marked rejected. `xpt clean` deletes the file with the rest of `.xpt/`.
- Rejecting a value twice writes nothing the second time and reports `already_rejected`, so `reject` is idempotent.
- `reject` rewrites the file whole to a temp file and moves it into place with `os.replace`, under the same `.xpt/accept.lock` as accept. A reader never sees a partial line, and two writers cannot drop each other's lines. A second writer fails on the held lock with exit code 2.

`xpt failures` marks rejected groups instead of hiding them. The test still fails, and pytest still reports it, so a group missing from `failures` would leave an agent hunting for it. The heading reads `a81f  tests/test_parser.py:14  mismatch  rejected  1 test`, the JSON group carries `"rejected": true`, and the footer counts them (`1 acceptable, 1 rejected`) and leaves them out of `Next: xpt accept`. `acceptable` keeps its meaning, that the status allows a write. `xpt show` says the group was rejected and how to accept it anyway. The pytest report adds `rejected` after the status and leaves the group out of its footer. `accept --all` and `accept --missing` leave rejected groups out of their selection, as `--missing` leaves out mismatches. Accepting one by its id still works, because naming an id is a deliberate decision that overrides the earlier one. Accept is idempotent. Running it twice leaves the file as it was after the first run, because the second run finds the fingerprint already matches the accepted value and has nothing to do.

`accept` without arguments does nothing and says so. Replacing existing expectations needs ids or `--all`. `--missing` fills only empty expectations, because creating one does not overwrite a reviewed value. The design first named the bulk flag `--all-mismatches` so that typing it would be deliberate. `--all` is just as explicit, also fills missing values, and is the flag agents reach for. `xpt failures` prints every diff before the footer suggests an accept, so the change is on screen when it is accepted.

### Verifying an accept

Agents in the benchmark ran the whole suite again after every accept, two to four pytest runs per task. `xpt accept --verify` reruns only the tests whose node ids the run record holds for the groups now `accepted` or `already_accepted`. It runs `sys.executable -m pytest <ids>` from the project root, after the accept lock is released, so the rerun can record. That rerun becomes the latest run, which is what an agent wants to read next. pytest's exit code decides: a nonzero code makes accept exit `1` and prints pytest's output. When nothing was written there is nothing to rerun, and no process starts. `--verify` with `--dry-run` is a usage error, because nothing would be written to verify.

A test that observed the call site may also fail for a reason the accept did not touch. That is still a failure to report, because the agent asked whether its tests pass now.

### Suspect values

Wrong accepts are the main risk of snapshot tests for agents, and the worst ones are values that are plainly failures: the code returned an error string or `None` and the agent accepted it to make the test pass. Accept refuses an actual value with the outcome `suspect` when one of these rules matches, unless `--force` (or `force=True`) is given:

- `exception`: a string that is an exception repr (`ValueError('bad')`), an exception line (`KeyError: 'id'`), or holds a traceback, where the expected value at that path was not one too.
- `none`: `None` where the expected value held anything else.
- `empty`: an empty list, tuple, set, or dict where the expected value had items.
- `nan`: `nan` where the expected value was a finite number.

`xpt.suspect.suspect(expected, actual)` walks the two `Value` trees together, matching list items by index, dict entries by key, and fields by name, and returns the first match's path and rule. It is pure, so the CLI, `--all`, `--missing`, and `pytest --expect-create` share it through `accept`. A missing expectation has no expected value, so only the `exception` rule applies. The reason names the path and rule, such as `value["items"] is empty where the expected value had items (rule: empty).`, and the footer names the `--force` command for those ids. The check runs before the file is read, so a dry run reports it too.

The rules are narrow on purpose. A false refusal costs one `--force`. A rule that fires on ordinary changes would teach agents to pass `--force` every time.

### Flaky values

A nondeterministic value fails on every run: a timestamp, an id, a list built from a set, or a race. Without help an agent finds that out by accepting the value and watching it fail on the next run. The stored runs already hold the evidence, because every failing run records the actual value.

`xpt.flaky` compares them. For each call site of a run it takes every stored run that recorded one actual value at that site with the same `arg_sha` and the same source digest, and walks consecutive values together. Each path where they differ is `Volatile`, with a kind that picks the suggestion:

- `timestamp`: both strings look like an ISO date or time, or both values are a recorded `datetime`, `date`, `time`, or `timedelta`, which are reported at the value and not at the field that changed. Suggest `ignore=['value["created_at"]']` or the `xpt_deterministic` fixture (see Deterministic tests).
- `uuid`: both strings are UUIDs, with or without dashes, or both values are a recorded `UUID`. Suggest `ignore=` for the path or the `xpt_deterministic` fixture. When the path can't be ignored, the line says to pin it with the fixture. Accept's refusal names the fixture after the keywords.
- `value`: any other differing scalar, a dict entry that comes and goes, or a list whose length changes. Suggest `ignore=` for the path, with list indexes widened to `[...]`. A path that does not end at a dict entry with a literal key can't be ignored, so the line says to make it deterministic in the test.
- `order`: a list or tuple whose items are the same multiset in another order. Suggest `unordered=True`.
- `float`: numbers, at least one a float. Suggest `atol=` the spread, rounded up to one significant digit.

A path with changes of more than one kind, or with changed paths inside it, is reported once as `value`.

- `xpt failures`, `xpt show`, and the pytest report add a line per path after the diff: `flaky: value["created_at"] looks like a timestamp and had 2 values in 2 runs with no source change. Consider ignore=['value["created_at"]'].` The JSON group carries `flaky`, a list of paths with `kind`, `values`, `runs`, `spread`, and `suggestion`.
- A group with volatile paths is not `offered`, so no footer and no `accept:` line suggests it. Accepting it would write a value the next run fails.
- `accept` refuses it with the outcome `suspect` and the rule `flaky`, after the suspect-value rules and with the same `--force` override. `--all`, `--missing`, `pytest --expect-create`, and `--expect-accept` share the guard through `accept`.
- `xpt flaky` lists every call site with volatile paths across the stored runs, each judged in the newest run that recorded it, with `--json`. It exits `1` when it finds one, like `failures`.

Open questions, resolved:

- **Is the fingerprint enough?** No. `arg_sha` covers only the expected argument, and an agent edits the code under test between runs all the time. A value that changed because the code changed would be called flaky, and the suggestion to ignore it would hide a real change. So the pytest controller stores a source digest with each run that recorded something: the sha256 of every `.py` file's path and contents under the root, skipping hidden directories, `__pycache__`, `node_modules`, `site-packages`, and virtual environments. Two runs count only when their digests match. The cost is a walk of the project's Python files once per failing session. Any edit, an accept included, starts the comparison over, which errs toward reporting nothing. Runs without a digest, from an older xpt or a process with no driver, never count. Data files outside `.py` are not hashed.
- **Do repeated runs count?** No. A run is one pytest session. `pytest --count` (pytest-repeat) runs the same call site several times in one session, and calls that disagree already make the group a `conflict` that names each value.
- **Which runs?** Every stored run with the same fingerprint and digest, not only earlier ones, because run ids started in the same second do not sort by time. Five runs are kept, so the comparison is bounded.

### Changed code

When an expectation fails, the agent has to decide whether the new value is intended or a bug, and the value diff alone does not say which edit caused it. `pytest --xpt-trace` records the project lines each test ran, and `xpt show` and `xpt accept` intersect them with `git diff`:

```
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

- **Recording.** `xpt.trace.Tracer` records lines of project files: `.py` files under the root, outside hidden directories, `__pycache__`, `node_modules`, `site-packages`, and virtual environments, the same rule as the source digest (`sources.project_file`). On Python 3.12 and later it uses `sys.monitoring`. A `PY_START` callback turns on `LINE` events only for project code objects and returns `DISABLE`, so installed code costs one callback per code object per test. Each `LINE` callback records the line and returns `DISABLE`, so a line costs one callback per test. `begin()` calls `sys.monitoring.restart_events()` at the start of each test to turn them back on. It takes the coverage tool id, or 4 or 3 when coverage.py holds it. On 3.11 it uses `sys.settrace` and `threading.settrace`, and the global trace function returns a local one only for project frames. 3.11 allows one trace function, so `--xpt-trace` beside coverage or a debugger is a usage error there.
- **Contexts.** The plugin calls `begin()` and `end()` around `pytest_runtest_protocol`, so a test's lines include its fixtures' setup and teardown. Module and class bodies, which are code without `CO_OPTIMIZED`, mostly run at import, before any test. Their lines are also kept as definitions, and a test counts the definitions of every file it ran a line in. A changed constant, default, or signature in a module the test used still shows. A module the test only read a name from, without running a line of it, does not.
- **Storage.** Only tests that recorded an observation are kept, because only those can be shown or accepted. At session end each process, controller or xdist worker, writes `runs/<run>/<worker>.trace.json` when it kept one: `{"schema_version": 1, "files": {path: sha256}, "tests": {node id: {path: [line, ...]}}}`. Nothing is shared during the run, as with observations. Reading joins every worker's file and validates every field, and the trace is only data.
- **The diff.** `xpt.changes` runs `git diff --unified=0 --relative <base> -- '*.py'` from the root, so staged and unstaged changes count, and `git ls-files --others --exclude-standard` adds each untracked `.py` file as one hunk. `--since REF` diffs against `REF`. The default is the merge base of `HEAD` with the default branch: `origin/HEAD`, then `main`, `master`, `origin/main`, `origin/master`. With none, it is `HEAD`, and in a repository with no commits the empty tree. git is optional. Without git, or outside a repository, the group gets one line, `changed code: unknown, not a git repository.`, and accept prints it once. git runs only when the run has a trace.
- **The intersection.** A hunk ran when the test ran one of its new lines. A removal ran when the test ran the line on either side of it. A file whose sha256 differs from the trace's changed after the run, so its line numbers no longer hold. It is left out and named on a `changed since the run, left out:` line. Hunks are grouped by the innermost function or class around their first line, from the working tree's syntax tree, `<module>` at module level, and printed with the common indentation removed. The default limits are 10 lines of 80 characters, and `--full` lifts them.
- **Nothing ran.** When the tests ran no changed line, show prints `no changed code ran; the expectation or the environment changed (diff against main)`. That is a strong hint that the expectation itself is stale, or that the failure comes from data, time, or the platform.
- **No trace.** `xpt show` says `no trace of these tests; run them with pytest --xpt-trace to see the changed code they ran`. `xpt accept` prints nothing for a group without a trace, so an untraced accept reads as before. `--json` always carries `changed_code`, with `status` `no_trace`.
- **Reuse.** `xpt coverage` (see Coverage in the diff) and `xpt affected` (see Affected tests) use the same parts: `trace.Tracer` for the lines, `changes.changes` for the diff, and `changes.function_at` for grouping by function. `xpt affected` records every test's lines.

Open questions, resolved:

- **Every run, or opt-in?** Opt-in for now, through `--xpt-trace`. Measured on this repository's suite on macOS, wall time as pytest reports it, on a machine with other work running:

  | Python | Suite | Without | With `--xpt-trace` |
  |---|---|---|---|
  | 3.11 (`sys.settrace`) | whole suite, 656 tests | 51.6 to 56.1 s | 53.8 to 55.9 s |
  | 3.13 (`sys.monitoring`) | whole suite | 63.1 s | 57.8 s |
  | 3.11 | 160 in-process tests (serialize, diff, source, rewrite, expect, group, convert) | 2.0 to 2.1 s | 3.4 to 4.1 s |
  | 3.13 | the same 160 | 2.8 s | 2.8 s |

  The whole suite spends most of its time in `pytester` subprocesses, which are not traced, so its numbers are within noise. The in-process tests run xpt's own code, all of it project code here, and show the real cost: about 70 to 100 percent on 3.11, where every project line calls a Python trace function, and about 2 percent on 3.12 and later, where each line calls back once per test. The 3.11 cost is too much to pay on every run of every project for a question asked only when an expectation fails, and an agent can rerun only the failed tests with `pytest --lf --xpt-trace` when it needs the answer. On 3.12 and later the cost is small enough that turning it on by default there is the likely next step, once a larger project confirms the number.
- **Accept or show?** Both. Accept prints the changed code before it writes, because accept is where a wrong decision lands, and it computes the hunks before writing so the accept's own edit does not show.

### Coverage in the diff

coverage.py's `term-missing` lists line ranges per file for the whole project, and the agent then opens each file to learn what the lines are. `xpt coverage` answers the question an agent has before it reports a change done: which of the lines I changed did no test run?

```
$ xpt coverage -- pytest
src/app/load.py read_rows: 2 of 9 changed lines not run
  31 |         if row.get("count") is None:
  32 |             raise ValueError("missing count")
2 of 9 changed lines not run, in 1 function (since main).
```

- **Scope.** A separate command, not part of the pytest report. The report answers "what failed", and adding coverage to it would cost every run a trace of every line and lengthen every footer. `xpt coverage [--since REF] [--json] [--full] -- <pytest command>` runs the given command from the root, its output on stderr, and reports on stdout. `--from-coverage [PATH]` reads coverage.py's data file, `.coverage` by default, instead of running anything, for a project that already runs coverage.py. The xpt process never imports the project.
- **The changed lines.** `changes.changes` gives the hunks against `--since`, with the same default and the same untracked files as Changed code. A changed line counts only when it holds code: a line of some code object compiled from the file's current source, through `co_lines()`, so blank lines, comments, `else:`, and other lines with no bytecode never show as not run. Compiling runs nothing. A file that does not compile is left out. When no changed line holds code, the command says so and exits 0 without running the tests.
- **The lines that ran.** `xpt coverage` sets `XPT_COVERAGE` to a temporary directory for the command. When the plugin sees it, `pytest_load_initial_conftests` starts a `trace.Tracer(session=True)` before conftest files import the project, so their module bodies count. The tracer keeps every project line it sees, in every context, beside the per-test contexts `--xpt-trace` uses, which stay as they were. On 3.12 and later each line costs one callback for the whole session, because no per-test `restart_events` turns it back on unless `--xpt-trace` is also given. On 3.11 it costs what `--xpt-trace` costs. Modules already imported when the tracer starts, such as `-p` plugins, are listed as `imported`, and their module and class body lines count as run. At session end each process, the controller and every xdist worker, writes `<pid>-<random>.json`, `{"schema_version": 1, "root": str, "lines": {path: [line]}, "imported": [path]}`, so workers never share a file. The reader rebases each path from that process's root onto the project root and drops paths outside it, so a pytest that a test starts in another directory adds nothing. A process that already runs a session tracer, such as an inline `pytester` run, does not start a second. No recording under `.xpt/` is needed.
- **coverage.py's data.** `--from-coverage` opens the SQLite file read-only with `sqlite3` and reads `file`, then `line_bits` (numbits: bit `i` of byte `b` is line `8b + i`), or `arc` when it measured branches, where every positive endpoint is a line that ran. Contexts are merged. coverage.py need not be installed. A changed file the data does not list was not measured, so it is named on a `not in the coverage data, left out:` line instead of reported as not run. A changed file modified after the data file was written is named on a `changed since the coverage data, left out:` line, because its line numbers may have moved.
- **Grouping and limits.** Changed lines are grouped by `changes.function_at`, the innermost function or class, `<module>` at module level. Text lists only the groups with lines that did not run, each with the source line as it is in the file, 10 lines of 80 characters per group, and a summary. `--full` lifts the limits. `--json` carries every group, including the ones that ran in full.
- **Exit codes.** `0` when every changed line ran and pytest passed, `1` when a changed line did not run or pytest exited nonzero, which the report says on its own line, and `2` for a usage error: no git or no repository, an unknown ref, a command that traced no lines, because it was not pytest or the plugin was not loaded, or a file that is not coverage.py data.
- **Branches.** Not reported yet. Never-taken branches need the possible arcs of each changed line, which coverage.py computes from its own analysis of the syntax tree. From xpt's tracer they could come from `sys.monitoring` `BRANCH` events on 3.12 and 3.13 and `BRANCH_LEFT` and `BRANCH_RIGHT` on 3.14, with the possible branches read from the bytecode of the changed code objects, and not at all on 3.11. That is a follow-up. A branch that was never taken often leaves a line of its body not run, which this already reports.
- **Subprocesses.** Lines run by processes the tests start, other than pytest, are not traced.

### Affected tests

The biggest repeated cost in an agent's loop is running the test suite. It runs everything after each edit, or guesses a subset and misses a regression. `xpt affected` lists the tests that ran the changed code, each with its reason, and `xpt affected -- pytest` runs only those (#70).

Decision: build it inside xpt, with the standard library only. pytest-testmon does the same job with coverage.py and SQLite, and reruns tests inside pytest (see `docs/research.md`). xpt already has the tracer and the diff from Changed code, so the map costs one more option and a module, with no dependency. What it adds over testmon is a diff against any git ref rather than the last run, a reason for every test, a selection that can be printed or read as JSON before anything runs, and a fallback to every test, with a reason, whenever the map can't be trusted. What it leaves to testmon is updating the map on every run, tracking installed packages, and tracing subprocesses.

- **The map.** `pytest --xpt-map` keeps every test's lines from `trace.Tracer`, not only those of failing tests. At session end each process, controller or xdist worker, writes `.xpt/map/<run>/<worker>.json`, as for observations, and the controller, which ends after its workers, joins them into `.xpt/map.json` with `os.replace` and deletes the parts. One map per project, replaced whole, because the question is about the code now, not about a run. It holds `HEAD`, the sha256 of each file the run imported or ran, with `\r\n` read as `\n` so a checkout that converts line endings matches the commit, and each test's lines. A session that ends with exit code 0 or 1 replaces the map; an interrupted or broken one keeps the old map. The map should come from a full run: a test it doesn't hold is selected only when its file is new or changed.
- **The diff.** The map's line numbers belong to the files it hashed, so a changed file is read through the old side of the diff, and `Hunk.old_start` carries it. The default base is the map's commit. A file that matches its hash is unchanged since the map, and its hunks, if any, are already in the map. A file that doesn't match must be in the diff, and its content at the base must match the hash, read with `git cat-file`. Otherwise the map is stale, `map is stale: N files changed since it was built that the diff against REF doesn't show (...)`, and every test runs.
- **Functions.** A hunk's lines of code, removed ones placed in the base's syntax tree and added ones in the working tree's, each sit in the body of an innermost function. A test is selected when it ran a line of that function's body in the base, which is pytest-testmon's granularity: an edit to a branch no test took still selects the tests that called the function. Blank lines and comments never select anything.
- **Import-time effects.** A line of code outside every function body runs when the module or class is defined: a constant, an import, a signature, a default, a decorator, a class attribute. The tracer sees those lines once, at import, mostly before any test, so they can't be attributed to a test. Such a change selects every test that ran a line of the file. A file the map run imported but no test ran a line of, such as a module of constants, can still be read by a test, so a change there runs every test. A module imported before tracing starts, by a root `conftest.py` or a plugin, isn't in the map at all, and a change to it selects nothing.
- **Test files.** A changed test file, one that holds a node id in the map, is selected whole, so tests added to it run. A new or untracked file named `test_*.py` or `*_test.py` is always selected. A deleted file selects the tests that ran it, and a test whose file was deleted is dropped.
- **Everything.** `conftest.py`, `pyproject.toml`, `setup.cfg`, `pytest.ini`, `tox.ini`, or any changed file that is not Python can change any test, so every test runs. So do no map, a stale map, and no git. Files under hidden directories, `__pycache__`, and virtual environments don't count, the same rule as the project's own files.
- **Running.** `xpt affected -- COMMAND` appends the selection to the command and exits with its code. `pytest` becomes `sys.executable -m pytest`, so it runs in xpt's environment. With nothing selected it runs nothing and exits `0`, because an empty pytest run exits `5` and would read as a failure; `--all-if-empty` runs everything instead.

Open questions, resolved:

- **What does xpt add beyond testmon?** See the decision above and the pytest-testmon entry in `docs/research.md`.
- **How are import-time effects attributed?** By file, not by test, as above. testmon does the same: its `<module>` block holds everything outside function bodies, and any test that ran a line of the file includes it.

## Persistence

```
.xpt/
  .gitignore            # "*", written on first use
  runs/<run>/<worker>.jsonl
  runs/<run>/source     # the project source digest, written by the pytest controller
  runs/<run>/<worker>.trace.json  # with --xpt-trace, the lines each failing test ran
  map.json              # with --xpt-map, the lines every test ran (see Affected tests)
  map/<run>/<worker>.json  # each process's part of the map until the controller joins them
  latest                # the run id of the newest complete run
  probes/<id>.jsonl     # the hits of one xpt probe (see Probing a line)
  decisions.jsonl       # reject decisions, one per line (see Rejection)
```

- Each pytest process, controller or xdist worker, appends to its own file. Nothing is shared, so nothing is locked. This follows "separate before serializing shared state."
- The controller writes `latest` at session end with `os.replace`, so a crashed run never becomes the latest. Just before, it writes the run's source digest to `runs/<run>/source` the same way (see Flaky values).
- Old runs are pruned to the last 5 by default.
- Each line is one JSON object with `"schema_version": 1`. A value that is absent (`expected` when missing) is left out of the object, so a recorded `None` stays distinct from no value. `atol` and `rtol` appear only when they are not 0, `ignore` (a list of path strings) only when set, `unordered` only when true, and `key` (a recorded value) only for a keyed call. Reading a run validates every line and names the file and line of a malformed one.
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
- The standard-library types xpt records natively (see Standard-library values) are the exception: accept adds `from datetime import datetime` and the like after the file's leading imports, one line per module with the names sorted, once per file however many groups need it. It adds none when the name appears anywhere in the file, bound or read, at any depth, or when the file has a star import, because the new module-level name could be shadowed by, or shadow, the one already there. Such a group is skipped with that reason. An import is never added below the call that needs it.
- Groups that are not acceptable are skipped with their `detail`, without reading the file.

SQLite would buy queries we do not need. It would also make concurrent writers our problem. JSON Lines is readable with `jq` and easy to delete.

## Setup and migration commands

`xpt init` and `xpt convert` lower the cost of starting with xpt, for a person or an agent.

- **One copy of the agent instructions.** The text lives in `src/xpt/instructions.md`, shipped in the wheel. A test checks that the block in `docs/agents.md` matches it byte for byte.
- **`init` is idempotent.** The block sits between `<!-- xpt:begin -->` and `<!-- xpt:end -->`. A second run finds it and writes nothing. A block someone edited is theirs: init reports it and never overwrites it. A heading-only match catches a block pasted by hand before markers existed.
- **`init` checks what it can see without running pytest.** It reads the installed pytest version and the `pytest11` entry point from the interpreter running xpt, and `addopts` from the configuration file pytest would pick and from the environment. Starting a pytest session would collect the project's tests, which can be slow or have side effects.
- **`convert` is conservative.** It uses only `ast` and byte offsets, the same splice approach as accept. Each edit is checked by parsing `expect(a, b)` from the new text and comparing its arguments' AST with the original operands, and the whole file must compile afterward. Anything that could change meaning is skipped with a reason, not guessed at. Float literals are skipped because `==` is exact on floats while a converted test invites `atol=`; the person converting decides.
- **`convert` takes no lock.** It writes only test files given on the command line, never `.xpt/`, so it does not create `.xpt/` as a side effect.

## MCP server

`xpt mcp` serves the review commands as MCP tools over stdio, so clients such as Claude Code and Codex list them with schemas and call them without reading `--help` (#69).

Decision: build it inside xpt, with the standard library only. MCP over stdio is JSON-RPC 2.0 with one message per line, which `json` and a read loop serve in about three hundred lines. An MCP SDK would break the no-runtime-dependencies rule, and a separate `xpt-mcp` package would be a second release for a few hundred lines. The benchmark comparison against the CLI that #69 asked for before shipping is not done: it needs paid agent runs, and is left to M5.

- **One schema.** Each tool calls the CLI command's handler in `xpt.cli` with a namespace built from the tool's arguments, and returns the object that command's `--json` prints, `schema_version` and `command` included. The tool result holds that object as JSON text in one `text` content item and, from protocol `2025-06-18` on, as `structuredContent` too. Nothing shells out.
- **Tools.** `xpt_failures(run?)`, `xpt_show(id, run?, full?)`, `xpt_accept(ids?, all?, missing?, dry_run?, force?, verify?, run?)`, `xpt_reject(ids, run?)`, `xpt_runs()`, and `xpt_flaky()`. `clean`, `init`, `convert`, and `run` are not tools: they set a project up or delete its state, which is a person's decision, not a step in the review loop. Each tool's `inputSchema` is a JSON Schema object with `additionalProperties: false`, and annotations mark the read tools `readOnlyHint`.
- **Errors.** A tool error, such as a bad argument, an unknown id, no recorded run, or a held lock, is a result with `isError: true` and the one-line message the CLI prints on stderr, so the model can read it and correct the call. An exit code of `1`, such as failures found or an accept that left a group stale, is not an error: the object says what happened. Protocol errors are JSON-RPC errors: `-32700` for a line that is not JSON, `-32600` for a message that is not a request, `-32601` for an unknown method, `-32602` for an unknown tool or params that are not an object, and `-32603` for a bug, whose traceback goes to stderr.
- **Versions.** The server knows `2025-11-25`, `2025-06-18`, `2025-03-26`, and `2024-11-05`. A client that asks for one of them gets it back, and any other gets `2025-11-25`, the newest, as the spec says a server should answer a version it does not support. The server implements `initialize`, `notifications/initialized`, `ping`, `tools/list`, and `tools/call`, and advertises only the `tools` capability, with `listChanged: false`. Other notifications are ignored. Batches, which `2025-06-18` removed, are answered `-32600`.
- **stdout is the protocol.** Only responses are written to stdout. A startup line goes to stderr, and anything printed during a tool call is redirected to stderr. `accept --verify` already captures pytest's output, and its child gets an empty stdin so it cannot read the protocol stream.
- **Accept keeps its guards.** `xpt_accept` refuses stale, suspect, and flaky values exactly as `xpt accept` does, and needs ids, `all`, or `missing`. It asks for no extra confirmation. A tool call is easier to make than a command, but MCP clients can already ask the person before a tool call, Claude Code does by default, and the annotations mark accept as not read-only. A second prompt inside xpt would only teach people to click through it. The tool's description says to read each diff before accepting and to pass `force` only for a value that is really intended.
- **`xpt init --mcp`** adds `{"mcpServers": {"xpt": {"command": "xpt", "args": ["mcp"]}}}` to `.mcp.json`, keeping every other key and server. An `xpt` entry that differs, such as one that runs `uvx xpt mcp`, is kept and reported, never replaced. A `.mcp.json` that is not a JSON object with an object under `mcpServers` is an error, and neither file is written. The merged file is rewritten with two-space indentation.

## Serialization, comparison, and source generation

These are separate functions over the `Value` tree:

- `serialize(obj) -> Value` turns a Python object into a tree. It raises `UnsupportedValue` with the path and type for anything it cannot represent: `result["handler"] is a function; xpt cannot record it. Register its class with xpt.register or expect a field of it instead.` It detects cycles by `id()`.
- `diff(expected, actual) -> [Change]` walks two trees. Dicts compare by key, sequences by index, sets as sorted members. The walk is pure and easy to test exhaustively.
- `to_source(Value, indent, width) -> str` prints a tree as a Python literal. Dict order is kept as observed. Set members are sorted. Floats use `repr`, which round-trips. It fits on one line when possible, and otherwise uses one element per line with a trailing comma, so the output already matches ruff's format.

Comparison is Python `==` on the reconstructed expected value, except for arrays, tensors, and pandas objects (see below), with `diff` used only to explain a failure. That keeps xpt's results identical to what a human writing `assert actual == expected` would get.

A dataclass prints as `Point(x=1, y=2)`. At record time we check that `Point` resolves to the same class in the test module's globals. If it does not, we mark the value unsupported instead of writing code that will raise `NameError`.

Adapters (`src/xpt/adapters.py`) turn NumPy, PyTorch, pandas, Pydantic, attrs, and protobuf values into the same constructor-call node a dataclass uses, so `diff`, `to_source`, and accept need no adapter code. `np.array([[1, 2]], dtype="int64")` is `{"numpy:array": {"": [[1, 2]], "dtype": "int64"}}`. The field named `""` is the positional argument and adds no step to a diff path, so a changed element reads `value[0][1]`. A dtype that must be a name rather than a string, such as `torch.float32`, is a `ref` node. Fields are matched by name in a diff, so a protobuf field set on only one side reads as added or removed.

The registry maps a class name such as `numpy.ndarray` to its adapter and is searched through the value's MRO. xpt never imports the libraries, so there are no extras and no runtime dependencies; the adapter runs only on objects that already exist. `xpt.register(cls, fields)` adds a user adapter that is checked first, by exact class, and writes `cls(**fields(obj))`. It requires `cls` to define `__eq__`, because a rebuilt object that compares by identity never equals the original.

Comparison is `==` everywhere except for arrays, tensors, and pandas objects, whose `==` is element-wise and broadcasts: `np.array([1]) == np.array([[1.0]])` is truthy. `equal` (`src/xpt/equal.py`) compares those by type, shape, dtype, and elements, and walks lists, tuples, dicts, and dataclasses item by item so an array nested in a dict is held to the same rule.

`equal` treats `nan` as equal to `nan`, in plain floats and element by element in arrays. Otherwise a recorded `float("nan")` could never pass, and accepting it would write a test that always fails. `diff` follows the same rule.

A value an adapter cannot express as one constructor call is wrapped in an xpt helper reached through `expect`, such as `expect.with_extensions(msg, extensions={...})` for protobuf extensions. Its type id is `xpt:expect.with_extensions`, so a file that imports `expect` already has the name.

Arrays over 10,000 elements are written as `expect.digest(...)`, the same kind of helper. A digest compares on kind, shape, dtype, device, and the sha256 of the little-endian, C-order bytes. Its `min`, `max`, and `mean` fields are only for the reader, so an edit to them never changes the verdict. A failure on an array of the same shape adds one line with the count of differing elements and the largest difference, computed by the diff walker from the changes it already found.

Acceptance never runs code from the record. It builds source text from the `Value` tree. It never calls `eval`, `pickle`, or the observed object's `__repr__`.

## Exceptions

`expect.raises(fn, expected?)` treats an exception as a value. `with expect.raises(expected?):` does the same for a block (see The block form). The call form calls `fn`, takes what it raised as the actual value, and goes through the same compare, record, and accept path as `expect()`. The call site's callee is `expect.raises` and its expected argument is the second positional or `expected=`, so identity, fingerprints, rewriting, and `pytest --expect-create` need nothing new.

- **What is recorded.** The exact type and `args`, and `__notes__` when set. `serialize` records any `BaseException` that is not a dataclass or a registered class, wherever it appears in a value. `args` is the field `"*"`, a tuple whose items are written as positional arguments: `{"builtins:ValueError": {"*": {"tuple": ["bad"]}}}` is `ValueError("bad")`. The field's path step is `.args`, the attribute that holds them, so a diff reads `value.args[0]` and an ignore path is written the same way. The traceback, cause, context, and other attributes are left out. They hold addresses, line numbers, and paths that change from run to run, and none of them can be passed to a constructor.
- **Comparison.** `equal` compares two exceptions whose class has no `__eq__` of its own by exact type, `args`, and notes, each with the same rules as any other value, so an `ExceptionGroup`'s children compare by value too. A class with its own `__eq__` keeps it. `subclass=True` accepts an instance of a subclass of the expected type. Before comparing, xpt rebuilds the caught exception's args and notes as the expected type, so a diff shows only args and notes, and accepting keeps the base type the test named.
- **Writing.** The default form is a constructor call, `ParseError("bad", 3)`. At record time xpt calls the class with the exception's `args` and checks that it gets back the same type and `args`. When it doesn't, because the class's `__init__` takes other arguments, or when the exception has notes, the value is `{"xpt:expect.exception": {"": {"ref": "module:Type"}, "*": {"tuple": [...]}, "notes": [...]}}`, written `expect.exception(Type, *args, notes=[...])`. That helper builds the instance with `Type.__new__` and sets `args` and `__notes__` without calling `__init__`, which is enough because only type, args, and notes are compared. The constructor check runs the project's own class on the failure path only. Nothing from the record is ever called or evaluated.
- **Did not raise.** A call that returns records status `returned`, with the return value as `actual`, the expected exception if any, and the failure message as `detail`. A `returned` group is never acceptable, because writing the return value as the expected exception would make a test that can never pass.
- **What passes through.** Every `Exception` and `SystemExit` is caught. `KeyboardInterrupt`, `GeneratorExit`, pytest's outcome exceptions, and other `BaseException` subclasses pass through unless the expected value is an instance of their type. A failed `expect()` inside `fn` always passes through.

Decisions on the questions issue #65 left open:

- **Classes with custom `__init__` signatures** are written through `expect.exception(Type, *args)`, not as a `{"type": ..., "args": ...}` dict. The test still reads as one expression that builds an exception, it compares with the same rules, and `expect.raises` needs no second shape of expected value.
- **`ExceptionGroup`** gets a tree of its children with no special case, because the children are part of its `args` and each one is recorded as an exception.
- **A type the test file can't name** is skipped with a reason naming the import to add, the same rule as a dataclass. Writing a qualified name the file doesn't bind would leave a `NameError`.
- **The context-manager form** came in #83, described in The block form below. It binds nothing with `as`. The exception is compared and recorded, and a test that needs its attributes can catch it itself.

### The block form

`with expect.raises(expected?):` runs the block, and `__exit__` compares what it raised with the rules above. It returns true, suppressing the exception, only when the exception matched. A mismatch raises `ExpectationFailed` with its context suppressed, so the report shows the diff rather than a second traceback. A block that finishes records status `returned` with no actual value.

- **Which form.** `expect.raises()` with no arguments, or with an exception instance first, returns a `Raises` context manager. Anything else first is the call form, and a callable is required. An exception class first is refused with how to write an instance, because `with expect.raises(ValueError):` from pytest habit would otherwise call the class.
- **Identity.** The site is the `expect.raises(...)` call that is the `with` item, so its callee, ordinal, and fingerprint are a call's like any other, and the two forms share one ordinal count. By the time `__exit__` runs, the frame's last instruction is in the block's cleanup, so `expect.raises` saves the frame's code, last instruction, line, and globals as a `site.WithItem` when it builds the context manager, and `locate` reads those. Each item of a multi-line or parenthesized `with` is its own call with its own position, so the rule needs no special case for them. When one `with` holds two `expect.raises()` items, the innermost catches the exception and the outer one sees none, as with any nested context managers.
- **The expected argument.** `site._collect` marks each call that is a `with` item, and `expected_arg` takes the first positional argument of a marked call instead of the second, or `expected=` for either. `arg_sha`, stale detection in accept, and `--expect-create` read it through that one function. Rewrite inserts the value as the first argument, before any keywords. A context manager whose call is not a `with` item, such as one assigned first and entered later, is `unlocatable`, since the call's first argument could not be told from the call form's.

## Standard-library values

`datetime`, `date`, `time`, `timedelta`, `timezone`, `zoneinfo.ZoneInfo`, `uuid.UUID`, `decimal.Decimal`, `pathlib` paths, and enum members are recorded natively (issue #82). `src/xpt/native.py` holds what is particular to each type. Each becomes the constructor-call node a dataclass uses, so grouping, rejection hashes, `ignore=`, and accept need no new shape, and nothing read from a record is evaluated.

- **Exact types.** A type matches only when `type(obj)` is the class itself, like the builtins. A subclass such as pandas' `Timestamp` stays unsupported, since writing `datetime(...)` for it would change the type under test. The classes are bound when xpt is imported, so the `xpt_deterministic` stand-in classes never confuse the check, and the values they build are real `datetime` objects.
- **Dates and times record every field** (`year` through `microsecond`, `days`, `seconds`, `microseconds`) so a diff reads `value.hour: 0 -> 12` and a path names a real attribute. `tzinfo` is left out when `None` and `fold` when 0, so a naive and an aware value differ by an added `.tzinfo`, which the suspect rules don't mistake for a `None` in place of data. The writer, through a `Signature` per type, puts the date and time fields positionally and drops trailing zeros: `datetime(2000, 1, 1)`, `time()`, `timedelta(seconds=90)`. `timedelta` keeps its normalized fields rather than hours or minutes, so what is written is what is stored.
- **Time zones.** `timezone.utc` is a `ref`, written `timezone.utc`. A fixed offset is `timezone(timedelta(...))`, with `name=` only when the zone was given a name. A `ZoneInfo` is recorded by its key and written `ZoneInfo("Europe/London")`; one built from a file has no key and is refused. Any other `tzinfo` is serialized as any value is, so a registered class works and anything else is unsupported at `.tzinfo`.
- **Text-backed values.** `UUID`, `Decimal`, paths, and `ZoneInfo` hold one string in the positional field `""`. A decimal is `str(Decimal)`, which keeps the exponent and the sign of zero: `Decimal("1.10")` is written as recorded even though it equals `Decimal("1.1")`. `norm` compares decimals by value, so a diff never reports `1.1 -> 1.10` next to a real change, and `equal` treats two decimal NaNs of the same kind as equal, like float NaN.
- **Paths.** `Path`, `PosixPath`, and `WindowsPath` all record as `pathlib:Path` with forward slashes, written `Path("a/b")`, which builds the native flavour on any system, so a test accepted on Linux passes on Windows. A pure path keeps its flavour, `PurePosixPath` or `PureWindowsPath`, because pure paths exist to name another system's paths, and a `PureWindowsPath` compares case-insensitively. Comparison stays Python's `==`: on Linux `Path("a") == PurePosixPath("a")`, and a `PurePath()` built on Linux is a `PurePosixPath`.
- **Enum members** are a `ref` to `Class.MEMBER`, written through the same import rule as a dataclass, so `Color.RED` needs `Color` in the file and is never imported for it. An `IntEnum` or `StrEnum` member is never written as its value. A member with no canonical name of its own, such as a combination of flags, is `Perm(3)`, a call with its value.
- **Diffs.** Dates, times, and durations diff by field. A `timezone`, `ZoneInfo`, `UUID`, `Decimal`, path, and any call whose only argument is one positional scalar, such as `Perm(3)`, diff as one value, because a UUID's text or an offset's seconds read better whole. The flaky report calls a changed date or time a `timestamp` and a changed UUID a `uuid`, at the value's own path, so the suggestion is `ignore=` for its dict entry or the `xpt_deterministic` fixture.
- **Reading a record** checks these shapes and refuses a malformed one with `InvalidValue`: unknown or missing fields, a non-integer field, an out-of-range or unnormalized date, time, or duration, a `fold` other than 1, a UUID, decimal, or path not in its canonical text. A check may build a `datetime` or `Decimal` from the typed fields to test the range; it never runs code from the record.
- **Imports.** Accept adds the import for these types when the file can't name them, as described in Writing the expected value. It is limited to these standard-library names, which are always importable; a project's own class can live anywhere, so its import is still left to the reader.

Comparison is still Python's `==` on the objects, so aware datetimes in different zones that name the same instant pass. When something else fails, the field-level diff can still list such a datetime, because it compares the recorded fields.

## Comparison options

`ignore=`, `unordered=`, and `key=` let an agent keep volatile or order-free data out of an expectation without hand-editing the recorded value. `xpt.options.Options` holds `ignore`, `unordered`, and the tolerance for one call, and `options()` parses the keyword arguments where they enter.

- **Ignore paths are diff paths.** A path is a string written exactly as a diff prints it, rooted at `value`: `'value["created"]'`, `'value.user["id"]'`, `'value["rows"][...]["id"]'`. We chose this over dotted strings and tuples. An agent reading a failure can paste the path from the diff line, with no translation step to get wrong. Dotted strings cannot tell the key `"0"` from the index `0`, or hold a key with a dot in it. Tuples are unambiguous but appear nowhere in xpt's output. Paths parse with `ast` and `ast.literal_eval`, so the key syntax is Python's and nothing is evaluated. `[...]` is the one addition, a wildcard over list items and dict values.
- **Ignore removes dict entries only.** Removing a list item would shift every later index, and removing a dataclass or model field would leave xpt unable to write the constructor. Both are refused with the alternative. Because the entries are gone before the value is recorded, the stored values, the diff, grouping, rejection hashes, and the accepted source all agree, and none of them needs to know about `ignore`.
- **Unordered sorts before comparing.** Every list and tuple is sorted by its canonical JSON, recursively, except inside arrays, tensors, pandas objects, and digests, whose order is their data. Sorting once makes accepted source stable. The diff still matches items as a multiset, so one inserted item reports one `add` instead of a shifted tail, and an item within the tolerance matches.
- **The pass check stays `==` first.** A call returns as soon as `equal` passes, so options cost nothing on the passing path. Only a failing call serializes, applies the options, and compares again.
- **Key keeps identity unchanged.** A keyed call site is still one site with one expected argument, so `arg_sha`, stale detection, and rewrite work as before. Each case is an entry in that argument's dict, and grouping merges the cases into one value (see Conflicting observations). Splitting one site into several identities per key would have needed a key in `SiteKey`, a key-aware rewrite, and per-key fingerprints, for no gain to the reviewer, who still edits one dict.

## Expecting output

`expect.output(source, expected)` builds a dict, `{"stdout": [...], "stderr": [...]}`, plus `"returncode"` for a `subprocess.CompletedProcess`, and passes it to `expect` with `_depth=1`. The call site's callee is `expect.output`, and the expected argument is still the second positional one, so identity, `arg_sha`, stale detection, `--expect-create`, and accept need nothing new. It lives in `xpt/output.py` and is attached to `Expect` as an attribute, like `digest`. A fixture is recognized by its `readouterr()` method, so the core never imports pytest.

- **Lines, not one string.** A stream is split on `\n`, and a final newline does not add an empty line. A one-line change then diffs at `value["stdout"][3]` instead of as one long string, and accepted source has one line per element.
- **Fixed normalizers.** In order: every spelling of the project root (as given and resolved, native and with forward slashes) becomes `<root>`; the system temporary directory becomes `<tmp>`, taking with it a `tmp********` directory from `mkdtemp` or pytest's `pytest-of-<user>/pytest-<n>/<test>` directory; trailing whitespace is dropped. A path only matches at a name boundary, so `/proj` does not rewrite `/project2`. We chose fixed rules over user-extensible ones: an extension point would need a way to record, show, and replay a user's rule at review time, and xpt never runs code from a record. Timestamps and durations are left to `ignore=` for whole streams or to the test, which can clean the text and call `expect` on the lines.
- **Normalization is said.** When any rule changed the output of a failing call, the observation's `detail` holds one line naming the rules that fired. The exception message, the pytest report, and `xpt failures` and `xpt show` print it under the diff. A passing call says nothing.
- **Reading consumes.** `readouterr()` empties the fixture's buffer, as it does when a test calls it. Re-emitting the text would duplicate it in a later `expect.output` and in pytest's own section. The failure's diff and record hold the whole output, so nothing is lost.
- **Bytes decode as UTF-8 with `backslashreplace`.** The record holds strings only, and an invalid byte stays visible as `\xff` rather than failing the call.

## Deterministic tests

`ignore=` handles volatile values after the fact. Most come from a few standard-library sources that can be pinned before the test runs, and a pinned value can be expected directly. `xpt.deterministic.deterministic(test_id, root=None)` is a context manager that pins them and yields a `Pinned` handle. The pytest plugin wraps it as the `xpt_deterministic` fixture, which passes the node id and `rootpath`, and `--xpt-deterministic` requests that fixture from an autouse fixture for every test. The module does not import pytest, so `unittest` uses it through `enterContext`.

Patched while the block runs:

- `time.time` and `time.time_ns` read the pinned clock, which starts at 2000-01-01T00:00:00 UTC and moves only through `Pinned.advance(seconds)`. It is held as integer nanoseconds, so repeated advances don't drift.
- `datetime.datetime` and `datetime.date` in the `datetime` module are replaced by subclasses whose `now`, `utcnow`, `today`, and `date.today` read the clock. Naive results are UTC wall time, so a test reads the same in every time zone. The subclasses' `__new__` returns real `datetime` and `date` objects, so recorded values, `type(value)`, pickling, and arithmetic are unchanged, and a metaclass makes `isinstance` and `issubclass` accept real instances.
- `uuid.uuid4` and `uuid.uuid1` return `UUID(int=n, version=v)` from a counter per version, starting at 1, so the first `uuid4()` is `00000000-0000-4000-8000-000000000001`. Their arguments are ignored.
- `random` is seeded with `f"xpt:{test_id}"`. A string seed is hashed with SHA-512, so the sequence does not depend on `PYTHONHASHSEED`. The generator's state is saved on entry and restored on exit, so the test does not shift the sequence of code that runs after it.
- Modules whose file is under `root`, outside hidden directories, `site-packages`, `dist-packages`, `node_modules`, and the standard library, and not part of xpt, pytest, pluggy, or xdist, have every global that is one of the originals rebound to its stand-in. This covers `from datetime import datetime` and `from time import time` in the code under test, which is how most projects import them. The walk is over `sys.modules` once per test and touches only project modules, which is cheaper than freezegun's walk of every module.

Restoration is a `finally` that puts back every attribute it replaced, in reverse, then the random state. The context manager takes an optional `patcher` with `setattr` and `setitem`, and the fixture passes the test's `monkeypatch`. Its patches and the test's then share one undo stack, so a test that also patches `time.time` unwinds in order whichever fixture it lists first. With two stacks, `monkeypatch` torn down after ours would put the stand-in back for the rest of the session. Nesting raises `RuntimeError`, since two blocks would fight over one global clock. xdist workers are separate processes and the seed comes from the node id, so a test pins the same values on any worker.

Not patched, on purpose:

- `time.monotonic`, `time.perf_counter`, and `time.sleep`. Timeouts, retries, and pytest's durations need them to move.
- `time.localtime`, `time.gmtime`, `time.strftime`, and `time.ctime` with no argument, `datetime.datetime.fromtimestamp`, and `os.urandom`, `secrets`, and `random.SystemRandom`.
- Installed packages and the standard library that imported a name directly, and C code that calls the clock without going through the module attribute. They see the real values.
- Third-party clocks such as `pendulum` or `arrow`.
- Code that compares `type(x) is datetime.datetime` while the block runs, which sees the stand-in class.
- Other threads are not isolated: the patch is process-wide while the block runs.

Open questions, resolved:

- **Delegate to freezegun or time-machine when installed?** No. Both would be an optional code path with its own behavior, so a test would pin different things depending on what else is installed, and the result on CI could differ from a laptop. freezegun walks the loaded modules on each start, which adds up when applied to every test, and time-machine is a compiled extension. The goal here is narrower: stable values for the handful of standard-library sources most tests touch, with no dependency. A test that needs C-level freezing can use time-machine directly instead of the fixture.
- **Warn when `PYTHONHASHSEED` is unset and a value depends on set order?** Not yet. Telling whether a recorded value depends on set or dict iteration order needs the same test run under two seeds, which one session can't do cheaply. The flaky report already finds the paths after two runs and suggests `unordered=True`. A warning tied to the fixture is a follow-up.

## Reporting

When xpt is installed, it replaces pytest's terminal output. Agents read every byte of a test run, and pytest's header, progress dots, rules, and full tracebacks spend tokens without saying more. The report stays readable for people, so it is the default. `--xpt-report=pytest`, or `XPT_REPORT=pytest`, keeps pytest's own output.

```
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
FAILED tests/test_math.py::test_double[0]  tests/test_math.py:22
  > assert double(x) == x + 2
  AssertionError: assert (0 * 2) == (0 + 2)
  stdout: debug line 0
  also fails: tests/test_math.py::test_double[1]  assert (1 * 2) == (1 + 2)
  also fails: tests/test_math.py::test_double[3]  assert (3 * 2) == (3 + 2)
6 failed, 2 passed in 0.31s. Next: xpt accept a81f
```

- Each failure is one block. The header names the test and the deepest frame in a project file, outside `site-packages` and pytest. ` in <function>` follows when that frame is not the test function. When the error is raised in a helper, the header shows the chain of project frames from the test down instead, as `path:line > path:line function`, keeping the first frame and the three nearest the error. The test's own name is left out because the node id has it. The next line is the source line at the deepest frame, after `> `. Setup and teardown errors say `ERROR` and name the phase. Collection errors say `ERROR <path> (collect)` and keep pytest's `E` lines.
- An `expect()` failure shows its short id, its status, the path diff, and a `flaky:` line for each path that changed between stored runs with the same source (see Flaky values). A group that is offered, recorded, and not written by this session ends with `accept: xpt accept <id>`. The footer already names every id, but an agent reading one block should not have to match ids against the footer to act on the diff it just read. The line and the footer's `Next:` come from the same set of pending groups, so they cannot disagree. Rejected, conflicting, and just-accepted groups get no line.
- A plain `assert left == right` on values xpt can serialize gets the same path diff, from `pytest_assertrepr_compare`, with each side labeled: `left 41, right 42`. It has no arrow, because `expected -> actual` would read backwards for `assert actual == expected`. Scalars other than multi-line strings, unsupported values, and other operators keep pytest's explanation.
- Any other exception is one line, `Type: message`, cut to 200 characters. An exception raised from another, with `raise X from Y` or inside an `except` block, adds `caused by: Type: message`. `raise X from None` adds nothing.
- Parametrized cases that fail at the same line with the same kind of error fold into the first case. Each other case gets an `also fails:` line with its full node id, which can be pasted into a pytest command, and the first detail line where it differs from the first case, without the shared exception type. The default limit of 10 applies, and the rest are counted.
- A failing test prints the last 5 lines of its captured stdout, stderr, and log.
- The footer is pytest's counts, warnings included, and duration, plus `Next: xpt accept <ids>` when recorded expect failures can be accepted and were not rejected, or `Next: xpt failures` when none can. With `--expect-create`, it first says `Filled N missing expectations: path:line ...`. With `--expect-accept`, it says `Accepted N expectations: path:line ...` and `Not accepted: <id> <status>, ...` for named ids that were stale, skipped, or unknown, and the lines just above the footer give each reason. `Next:` covers only what was not written. When every expect failure was written, it says `Next: run the tests.` With `--xpt-report=pytest`, the same lines print after pytest's summary.
- `-v` raises the limits to 50 changes, 400 characters per value, 20 output lines, and 1000 characters per message. `-vv` lifts them and adds pytest's traceback. `-q` prints the same compact report.
- `-v` and `-vv` also keep pytest's line per test. `-r` with anything but the default `fE` keeps pytest's short summary beside the report, and `-rP`/`-rA` keep its PASSES section. `--no-summary` prints only the footer. `--no-header` and `-q` drop the plugin header lines.
- Everything else pytest prints still prints: the warnings summary, plugin header lines and terminal summaries, `--durations`, `-x` stops, keyboard interrupts, interrupted collection, `--lf` messages, `--setup-show`, live logs, the debugger, and xdist's worker status lines. Exit codes are pytest's. Under xdist the controller prints one report.
- `--tb` other than `auto`, `-l`, and `--full-trace` ask for pytest's failure sections, so those runs keep pytest's report. So do runs that don't run tests: `--co`, `--fixtures`, `--fixtures-per-test`, `--markers`, `--setup-only`, `--setup-plan`, `--help`, and `--version`. `--xpt-json` with one of these is a usage error, because there is no xpt report to write.
- `--xpt-json PATH` also writes the report as one JSON object with `"schema_version": 1`, `exit_code`, `summary`, `counts`, `duration`, `run`, `created`, `accepted`, and `failures`. `created` is null without `--expect-create`. With it, `created` holds `outcomes` and `changed` in the same form as `xpt accept --json`. `accepted` is the same for `--expect-accept`, plus `unknown`. A held `.xpt/accept.lock` skips every missing group with the lock's message instead of failing the session. Each failure has its test, phase, location, `callers`, `source`, `cause`, `detail`, captured `output`, and full `traceback`, with no limits. An expect failure adds its `group` in the same form as `xpt failures --json`, and a compared assert adds `changes` and `unchanged`. A compared assert's changes name their sides `left` and `right`, as the text does. `op` reads from left to right, so `add` is a value only the right side has.

The expect limits are 10 changes per expectation and 80 characters per value, cut with a length marker. `xpt show --full` lifts both.

A change whose sides are both strings, one of them spanning lines, prints as a unified line diff instead of two cut values: `-` lines are expected (or left), `+` lines actual (or right), with one line of context and the `@@` hunk headers that give line numbers. The file headers are left out. Each diff line is cut to the value width, and a diff shows at most twice the change limit in lines, so 20 by default and 100 with `-v`. This holds for a plain `assert` on two multi-line strings too, which pytest would otherwise explain with its own character diff. In `--json` and `--xpt-json`, such a change adds `lines` with the whole diff.

```
  value: lines differ, - expected + actual
    @@ -3,3 +3,3 @@
     | C | 0.82 |
    -| D | 0.83 |
    +| D | 0.93 |
     | E | 0.84 |
  1 changed, 0 unchanged
```

`xpt failures` prints the same block per group, a one-line footer with the counts, and the next command:

```
a81f  tests/test_parser.py:14  mismatch  2 tests
  value.operator: "addition" -> "subtraction"
  1 changed, 42 unchanged
1 failure in run 20260101T120000-ab12 (1 mismatch), 1 acceptable. Next: xpt accept a81f
```

There is no color and no banner. `--json` prints one JSON object on stdout with `"schema_version": 1` and `"command"`. Values use the recorded JSON form, and `expected` is left out when there is none, so a recorded `None` stays distinct. A mismatch adds `changes` (path, op, expected, actual) and `unchanged`. Accept adds `outcomes` and `changed`. Reject adds `outcomes`. Each group carries `rejected` and `flaky`.

The CLI handlers are thin wrappers over `xpt.api`, which re-exports `load_run`, `group`, `accept`, `reject`, `load_rejections`, `mark`, `diff`, and their data types. `xpt mcp` (see MCP server) calls the CLI handlers themselves, so its tools return the same objects.

### How the report fits into pytest

The report has to work on every pytest a project might pin, and it can't take away anything pytest prints. Three designs were compared on pytest 8.0 through 9.2:

- **A separate reporter of our own**, registered as `terminalreporter` in place of pytest's. pytest's reporter implements about 16 hooks, and the set changes between releases: 8.0 has `pytest_runtest_logfinish`, 9.2 drops it and adds a `pytest_runtestloop` wrapper. Exit messages, `--max-warnings`, `--lf` messages, keyboard interrupts, and the warnings summary all live in those hooks. Plugins also read the reporter's attributes: `_tw` (the debugger), `stats`, `write_sep`, `section`, `write_line`, `rewrite`, `ensure_newline`, `hasmarkup` and `isatty` (xdist), `showfspath`, `startpath`, and `reported_progress`, and `config.get_terminal_writer()` returns `_tw`. A reporter of our own would have to reimplement all of that and follow every release.
- **A wrapper that delegates to pytest's reporter.** It inherits nothing, so it needs the same list, kept by hand.
- **pytest's reporter, subclassed in place** (chosen). At configure time xpt changes the registered reporter's class to `AgentReporter`, a subclass, and registers the same object again so pluggy sees the new hooks. Objects that already hold the reporter, such as xdist's `DSession.terminal`, keep it. The subclass overrides only methods pytest names publicly: `summary_failures`, `summary_errors`, `short_test_summary`, `summary_stats`, `report_collect`, `pytest_collection`, the per-test log hooks, `showheader`, and a `pytest_report_teststatus` wrapper that blanks the progress letter while keeping the category, so `stats` stay pytest's. Every other hook and attribute is pytest's own code for that release, so there is no list of pytest's surface to maintain.

The subclass depends only on those method names, which are the same across the tested range. `pytest.TerminalReporter` is public from 8.4, and older releases import it from `_pytest.terminal`. 8.0 is the floor because the report uses `Config.get_verbosity`. On an older pytest the plugin prints pytest's report and a one-line notice.

If the swap raises, xpt puts pytest's class back, registers it again if needed, and prints a notice. If rendering raises, `summary_stats` prints pytest's failure sections, short summary, and final line, then the notice. Neither path changes the exit code. pytest-sugar replaces the reporter with its own class before xpt's `trylast` configure runs, so xpt sees a reporter that isn't pytest's and stands aside.

`tests/test_report_compat.py` holds this to account. It runs a suite with every outcome, a plugin that writes through the reporter, and 32 option sets, once with `--xpt-report=pytest` and once with the xpt report, and asserts equal exit codes, equal stderr, and that every stock line outside the replaced sections also appears in the xpt run. CI runs the whole test suite on each pytest minor release from 8.0 and on the oldest and newest supported pytest-xdist, once under `-n 4`, and the compatibility tests with pytest-sugar installed.

Known gaps: pytest 9.2's OSC 9;4 terminal progress indicator stays at 0%, because the report doesn't advance `reported_progress`. Behavior on a real terminal, where pytest rewrites lines, is tested only through the same code paths a pipe uses.

### Tracebacks outside pytest

Agents also read tracebacks from scripts, CLIs, servers, and `python -c`, which hold every site-packages frame, a block per chained exception, and a thousand frames for a recursion error. `xpt.traceback` prints an uncaught exception in the report's shape instead, from `sys.excepthook` and `threading.excepthook`:

```
RuntimeError: load failed
  at app/chain.py:12 in load
  > raise RuntimeError("load failed")
  from chained.py:3 in <module>
  during ValueError: no count at app/chain.py:5
  caused by KeyError: 'count' at app/chain.py:3
  locals:
    row = {"name": "ada"}
```

- **Shared with the report.** `xpt.frames` holds what both outputs decide: which file is a project file (under the root and outside `site-packages` and `dist-packages`), how the caller chain is cut (the outermost frame and the 3 nearest), how an exception is named, and how its chain is walked. The limits are the report's `limits(verbosity)`: 200 characters per message, 80 per value, and 10 locals or chained exceptions, raised by `verbose`. Message lines come from the same function as the report's `Raised` detail. `tests/test_agent_report.py` pins the report on the same crash fixtures the traceback is tested on.
- **What the hook adds.** The report shows one failure in a test, so it needs neither a library hop nor a recursion count. The hook prints `via pkg/module.py` for the first library module the deepest project frame called into, folds a repeated frame or a repeated cycle of up to 8 frames into one entry with a count, follows the whole chain one line per link (`caused by` for `from`, `during` for an implicit context) and lists an exception group's members as `member` lines, and prints the deepest project frame's locals through `serialize` and `show`. A value that cannot be serialized prints as `<module.Type>`, never as a `repr`, which would carry addresses and arbitrary output. Outside pytest the standard library, the installed packages, and the running Python's script directory, which can sit inside the project as `.venv/`, are also not project files. Code typed with `-c` is.
- **Project root.** pytest's `rootdir` is not there, so the root is the nearest directory up from the start directory that holds `pyproject.toml` or `.git`, or the start directory itself. The start directory is read when the process starts, so a later `chdir` does not move the root.
- **Activation: opt in, without a `.pth` file.** A `.pth` file in site-packages would run in every Python process of the environment, including pytest, editors, and tools that never asked, and a test library should not change how unrelated programs crash. So it is opt in, two ways. `xpt.traceback.install()` installs the hooks when `XPT_TRACEBACK` is set, so a program can call it unconditionally and keep Python's traceback for people. `xpt run -- <command>` sets `XPT_TRACEBACK` and puts `xpt/startup/` first on `PYTHONPATH`. Its `sitecustomize.py` installs the hooks in every Python process the command starts, including grandchildren. Python imports `sitecustomize` from `sys.path` at startup without any flag, so this works for `python script.py`, `python -m`, console scripts, and servers alike. Alternatives were worse: `PYTHONSTARTUP` runs only for interactive sessions, rewriting the command into `python -c` works only when the command is Python itself, and `usercustomize` is skipped in virtual environments.
- **Startup cost and shadowing.** Importing xpt takes about 40 ms, so the `sitecustomize` imports nothing from xpt until an exception reaches a hook. It sets `_thread._excepthook`, which `threading` copies as its `excepthook` when first imported, so it does not import `threading` early, which gevent's monkey patching warns about. It removes its directory from `sys.path` and imports the `sitecustomize` it shadows, if any, before installing, so a project's own `sitecustomize` still runs and a hook it sets is replaced only because `xpt run` asked. A process that can't import xpt, such as one from another environment, prints Python's traceback.
- **Same exit and destination.** The interpreter, not the hook, sets the exit code, so it stays 1 for an uncaught exception, and a thread's exception does not change it. The hook writes to `sys.stderr` and prints nothing when that is `None`, as Python's does. A thread's block starts with `Exception in thread <name>:` and `SystemExit` in a thread prints nothing. If formatting raises, the hook prints Python's traceback and a notice.
- **Fallback.** `XPT_TRACEBACK=full`, `0`, or unset keep Python's traceback, and `xpt run -vv` sets `full`. `xpt run -v` and `XPT_TRACEBACK=verbose` raise the limits as `-v` does under pytest. `python -I` and `-E` ignore `PYTHONPATH`, so `xpt run` can't reach them.

On the crash fixtures in `tests/crash_project.py`, the compact traceback is about a fifth of Python's on stderr, and the tests require at most a third.

### Probing a line

Agents can't drive pdb, so they debug by adding `print` calls, rerunning, and deleting the prints, which edits source and costs a run per round. `xpt probe FILE:LINE [--expr EXPR] [--when COND] [--max-hits N] [--json] -- command` records the values at one line while the command runs (#59):

```
app/window.py:6 hit 2 times
  hit 1 in window_sums from tests/test_window.py:5 in test_window_sums:
    values = [1, 2, 3, 4]
    size = 2
    sums = []
    start = 0
    chunk = [1, 2]
  hit 2: sums[0]: added 3; start: 0 -> 1; chunk[0]: 1 -> 2; chunk[1]: 2 -> 3
Recorded in .xpt/probes/20261126T074616-6753.jsonl. Exit code 1.
```

- **Scope: inside xpt.** A separate package would need the value trees, `serialize`, `diff`, `show`, the frame filtering, and the `sitecustomize` activation, which is most of what it does. It ships as the `xpt probe` subcommand, and the code lives in `xpt.probe` and `xpt/startup/_xpt_probe.py`; `cli.py` only routes to it.
- **Activation.** The same as `xpt run`: `startup/` goes first on `PYTHONPATH` and `XPT_PROBE` holds the configuration as JSON, so every Python process the command starts, including xdist workers and subprocesses, loads the tracer. The `sitecustomize` loads `_xpt_probe.py` by path, after chaining any `sitecustomize` it shadows. When the process can't import xpt, the tracer appends the directory xpt was imported from in the parent to the end of `sys.path` on the first hit, so it never shadows the process's own packages.
- **Tracing cost.** The tracer uses only the standard library until the line runs. On 3.12 and later it uses `sys.monitoring`: a `PY_START` callback sees each code object once, turns on local `LINE` events for the code objects of the target file whose `co_lines()` hold the line, and returns `DISABLE` everywhere; the `LINE` callback returns `DISABLE` for every other line. So code objects are found lazily as they start, with no import hook, and a program runs at nearly full speed until the line is hit. On 3.11 a `sys.settrace` and `threading.settrace` function returns a line tracer only for those code objects, and caches the file check by filename. It still costs a Python call per function call: a loop of two million small calls went from about 0.07 s to 0.3 to 0.5 s. `XPT_PROBE_TRACER=settrace` forces the 3.11 path on later versions, which the tests use to cover both on every CI Python.
- **A hit.** The values are read before the line runs, as at a breakpoint. `--when` and `--expr` are compiled before the command starts, so a syntax error is a usage error, and are evaluated in the frame's globals and locals. They are text the user typed, run in the user's own program. A `--when` that raises counts as false and is reported with how often it raised. A hit records the locals that the compact traceback would print (no dunders, modules, functions, or classes), or the one `--expr` value, as value trees. A value `serialize` refuses records the same `<module.Type>` placeholder the traceback prints, and a value over 100,000 JSON characters records its type and size. The hit also records the project call chain from `xpt.frames`, through the traceback's `Hooks.place`. A re-entrancy guard per thread stops a hit from probing itself when `--expr` calls back into the line.
- **Aggregation.** Each process appends to its own file in `.xpt/probes/<id>.d/` and flushes each line, so nothing is shared or locked. At exit it writes a line with its counts: how often the line ran, how often `--when` was true, and its first `--when` error. A forked child resets its counts. When the command ends, `xpt probe` merges the files into `.xpt/probes/<id>.jsonl`: a header, the hits in time order, and the process lines. A process that exited without its count line (`os._exit`) counts its recorded hits. `--max-hits` applies per process, because a global count would need IPC; hits past it are still counted. The last five probes are kept.
- **Output.** `path:line hit N times`, the first hit's function, `from` chain, and locals, then one line per later hit with the paths that changed since the hit before, from the structured diff, as `path: old -> new` with the local's name as the path's root. Diffing against the previous hit rather than the first shows a loop's progress one step at a time. A line that can never run, found by compiling the file and collecting `co_lines()` from every nested code object, is refused before the command runs with the nearest executable lines. A line that never ran, a `--when` that was never true, and a command whose processes never loaded the probe (`python -I`) each get their own message. The summary goes to stderr after the command, so its stdout is unchanged. `--json` prints one object on stdout and sends the command's stdout to stderr. The exit code is the command's.
- **Recorded values are data.** Reading a probe file rebuilds values with `from_json` and nothing else. `tests/test_probe.py` checks that a recorded string holding code stays a string.
- **No benchmark.** The issue asked for a token comparison with print debugging through the benchmark harness. That needs paid agent runs, so instead `tests/probe_project.py` seeds an off-by-one in a sliding-window sum, and a test pins the probe output that shows it: the line ran twice for three windows, and `start` stopped at 1.

## Environment report

Agents lose turns to environment problems that look like code problems: the wrong interpreter, a local `json.py` shadowing the standard library, or an installed version that differs from the lockfile. Each shows up as a confusing import error or a wrong-API failure, and the agent debugs the code. `xpt env` reports them in one call.

- **Scope: a subcommand, not a separate package.** It is far from expect testing, but it answers the question an agent asks right after a failing run it can't explain, so it belongs next to `xpt failures`. It costs no dependency and no startup time for anything else: one module and a few lockfile parsers in the standard library. A separate package would be one more tool to install and to tell agents about, for about 600 lines. It stays read-only, never imports a project module, and never touches the network, so running it is always safe. If it grows past reporting, such as fixing the environment, that part is a separate tool.
- **Problems only.** It prints one line for the interpreter and one line per problem, so a clean environment costs one line and an agent can run it freely. It exits `1` on a problem, like `failures`.
- **Shadowing** covers the root and `sys.path[0]`, the two directories Python puts in front of the standard library and site-packages. Built-in modules such as `sys` can't be shadowed and are skipped. The project's own distribution is not a shadow of itself. Test directories that pytest's `prepend` import mode puts on `sys.path` are not searched.
- **Drift needs to know what should be installed.** A uv lock is universal: it lists packages for every platform, extra, and group. Evaluating PEP 508 markers needs a marker and version parser that the standard library does not have, so drift reports every mismatch of an installed package, and reports a package missing only when every environment needs it: in `uv.lock`, reachable from the project through edges with no marker; in `poetry.lock`, in `main`, not optional, and without markers; in `requirements*.txt`, a pinned line without a marker. The cost is that a missing package behind a marker, or in a group that was meant to be installed, goes unreported.
- **`xpt env NAME` never imports.** It asks the meta path finders, with the root first on `sys.path`, and walks a dotted name through each package's `submodule_search_locations` with `PathFinder`, because `importlib.util.find_spec("a.b")` imports `a`. The owning distribution is the one whose `RECORD` lists the file, or an editable install whose source directory holds it.
- **Not in the pytest plugin yet.** A shadowing line when a test fails to import would catch the problem where it appears, but the xpt report has no slot for it today. It is a follow-up.

## Installed API listing

Agents call library APIs as they remember them, not as they are in the installed version, and learn otherwise from a failing run. `help()` and pydoc print whole docstrings, and reading the source costs more. `xpt api TARGET` prints one line per public name instead: signature and first docstring sentence, methods indented under their class, the installed version first. The CLI reference documents the output.

```text
$ xpt api argparse.ArgumentParser --grep intermixed
argparse stdlib (Python 3.11.18)
ArgumentParser(prog=None, usage=None, description=None, epilog=None, parents=[], ...)  Object for parsing command line strings into Python objects.
  .parse_intermixed_args(args=None, namespace=None)
  .parse_known_intermixed_args(args=None, namespace=None)
```

- **Static by default.** Importing a package runs its code: network clients that read configuration, plugins that register themselves, heavy native libraries that take seconds to load. Listing an API should do none of that. `xpt.apisource` finds a module's file through each finder on `sys.meta_path` without importing the module or its parents (`importlib.util.find_spec` on a dotted name imports the parents), and parses it with `ast`. Only the top-level statements are read, the way an import would bind them: a `def` or `class` wins over an import or assignment of the same name, `try` and `if` bodies are walked, and `sys.version_info` and `sys.platform` branches are decided for the running Python, as stubs need. `xpt.apistatic` follows `__all__` (lists, `+`, `+=`, `.extend`, and `other.__all__`), star imports, and `from x import y` re-exports to the defining module, and reads a class's constructor from `__init__`, `__new__`, dataclass and NamedTuple fields, or the first base it can resolve.
- **Stubs first.** A `.pyi` next to the source, a `.pyi` next to an extension module, or a `<package>-stubs` package is read instead of the source. Stubs exist precisely where the source can't show the API, as in compiled packages.
- **Say what was not read.** A name the source binds at import time, one re-exported from an extension module with no stub, or a constructor inherited from outside the source is not guessed. It prints as `unresolved: <reason>`, or `(...)` for a constructor, and a last line counts them and names `--import`. A module with no Python source prints a note instead of names.
- **`--import` is the fallback, not a mode.** It imports the module and reads with `inspect.signature` only what the static pass left unresolved, plus the whole module when it has no source and `__all__` when it is computed. Static results win where both exist, so `--import` never makes the output less precise.
- **Bounded.** A signature longer than 80 characters drops optional parameters and the annotations of required ones, defaults past 20 characters print as `...`, and docstrings stop at the first sentence or 72 characters. At most 80 names print by default, each member counting, with a line that says how many were left out and to use `--grep`. `--json` keeps full signatures and every parameter.
- **Inherited members** come only from bases in the same top-level package. Listing every `BaseModel` method under each Pydantic model would bury the class's own API.
- **Limits.** A decorator that changes a signature, such as a custom wrapper without `functools.wraps`, is not seen statically. Class attributes other than properties and enum members are not listed. The listing describes the interpreter running xpt, so it must run from the project's environment.

Size against pydoc's plain-text `help()` output for the same target, on Python 3.11 (characters / lines):

| Target | `help()` | `xpt api` | `xpt api --limit 0` | Ratio, full listing |
|---|---|---|---|---|
| `json` | 24,346 / 560 | 1,469 / 13 | 1,469 / 13 | 17x |
| `pathlib.Path` | 10,243 / 315 | 4,415 / 60 | 4,415 / 60 | 2x |
| `argparse.ArgumentParser` | 4,583 / 119 | 855 / 20 | 855 / 20 | 5x |
| `pytest` 9.2.0 | 353,560 / 8,860 | 6,793 / 83 | 61,026 / 722 | 6x |
| `hypothesis` 6.170.15 | 47,587 / 1,075 | 4,529 / 63 | 4,529 / 63 | 11x |

`pathlib.Path` gains the least because pydoc already prints it compactly and most of xpt's lines are one-sentence summaries of its 60 methods. `pytest` shows why the default cap exists: its `__all__` names 91 objects, 66 of them classes. Each listing above took 0.1 to 0.25 seconds, most of it Python startup.

## Concurrency

- xdist: workers record and never write source. The controller sees every worker's file at read time. No IPC is needed. `--expect-create` and `--expect-accept` run in the controller's `pytest_sessionfinish`, after every worker has finished. The report drops the `[gw0] platform -- Python ...` line pytest puts at the top of each failure's text under xdist, so the report and `--xpt-json` match a run without xdist.
- Two pytest sessions at once in one project each get their own run directory. `latest` names whichever finished last. That is acceptable, and `--run <id>` selects another.
- Accept while pytest runs: accept creates `.xpt/accept.lock` with `O_CREAT | O_EXCL`, which behaves the same on POSIX and Windows, and deletes it when done. A second accept fails with a message naming the lock file. A crash can leave the file behind, so the message says to delete it if no accept is running. A test run racing an accept is caught by the fingerprint check on the next accept. `reject` and `clean` take the same lock, so the error says another xpt command is writing to `.xpt/`.

## Risks

- **Rubber-stamping.** The tool makes accepting cheap, so agents will accept regressions. Mitigations: no bulk accept by default, the diff shown at accept time, `--dry-run`, refusing suspect values without `--force`, and, with `--xpt-trace`, the changed code the failing test ran (see Changed code). The benchmark (M5) measures how often wrong accepts happen. We do not claim to prevent them.
- **Dynamic call sites.** `expect` called through a helper reports the helper's frame. We support `expect(..., _depth=1)` the way expecttest supports `skip`. A call created through `functools.partial` or `exec` has no source to rewrite. We record it as unsupported for rewriting, and the comparison still runs.
- **Generated or non-file sources** (doctests, notebooks, `python -c`): same as above.
- **Floats and nondeterminism.** Dict order, set order, timestamps, ids, and memory addresses make snapshots flaky. Sorting covers sets, `unordered=` covers lists, `ignore=` covers volatile dict entries, and adapters cover the rest. `xpt flaky` finds the paths that need them from the stored runs, and accept refuses them. `xpt_deterministic` pins the clock, `random`, and `uuid` in the standard library so the values don't vary in the first place. Default `repr` of arbitrary objects is never used, because it embeds addresses.
- **Large values.** Records are capped at 1 MB per observation by default. Larger values are recorded as a hash plus a summary and cannot be inline-accepted.
- **Sensitive data.** `.xpt/` holds real values from test runs. It is gitignored on creation, pruned to 5 runs, and removed by `xpt clean`. The README says so plainly.
- **Python 3.11 floor** excludes 3.10 users. 3.10 is end of life, so this is acceptable.
- **`co_positions` can be disabled** with `-X no_debug_ranges`. We detect `None` positions and fall back to line-only matching, refusing when a line holds more than one call.

## Minimum public API

```python
from xpt import expect

expect(actual)  # missing: fails, records
expect(actual, expected)  # compares with ==, records on mismatch
expect.raises(fn)  # calls fn; missing: fails, records what it raised
expect.raises(fn, ValueError("bad"))  # compares type, args, and notes; records on mismatch
with expect.raises(ValueError("bad")):  # the same for a block; the expected value comes first
    ...
```

Keyword-only arguments change how a call compares: `atol=`, `rtol=`, `ignore=`, `unordered=`, and `key=`. They stay in the source, so a reviewer can see them, and they are recorded with each observation, so review and accept compare the same way the test did. `expect.raises` takes `subclass=` and `ignore=`.

Recorded values may also use helpers reached through `expect`, so a file that imports `expect` can always name them: `expect.digest(...)`, `expect.with_extensions(...)`, and `expect.exception(Type, *args, notes=[...])`.

`expect.output(capsys)` expects captured stdout and stderr, or a `CompletedProcess`, as lists of lines (see Expecting output).

pytest plugin options: `--expect-create`, `--expect-accept=ID[,ID...]`, `--expect-no-record`, `--xpt-report=xpt|pytest`, `--xpt-json PATH`, `--xpt-deterministic`, `--xpt-trace`, and `--xpt-map`. The plugin's fixture is `xpt_deterministic` (see Deterministic tests). unittest works with no plugin, because `expect` raises `AssertionError` and records through the same process-level recorder. Its test id falls back to the qualname.

## Roadmap

Each milestone ships on its own and is tracked as GitHub issues.

- **M0. Scaffold.** uv, ruff, ty, pytest, CI on 3.11 to 3.14, MIT license.
- **M1. Core assertion.** `Value`, `serialize`, `diff`, `to_source` for builtins and dataclasses. `expect()` compares and raises with the compact message. No recording yet.
- **M2. Record and accept.** Call-site identity, JSON Lines recorder, `failures` / `show` / `accept` / `reject` / `clean`, fingerprint checks, atomic multi-edit writes, `--expect-create`. This is the differentiator and the MVP line.
- **M3. Reporting polish and comparison options.** pytest longrepr replacement, limits, `ignore=`, `unordered=`, `key=` for parametrized tests. The comparison options are done.
- **M4. Adapters and xdist.** NumPy, pandas, PyTorch, Pydantic, attrs, and protobuf adapters are done. Tolerances (`atol=`, `rtol=`) are done. Large-array digests and mismatch counts are done. The xdist test matrix is done: `tests/test_xdist.py` runs a suite under `-n 2` and without xdist and checks that every worker's observations group as one run, that conflicting values from two workers are one conflict that accept refuses, that `--expect-create` and `--expect-accept` write once from the controller, that the report and `--xpt-json` match, and that `--expect-no-record` writes nothing. CI also runs the whole suite with `-n 4`.
- **M5. Agent benchmark.** Fixture repos with seeded regressions. Claude Code and Codex run headless against plain asserts, expecttest, inline-snapshot, and xpt. We measure tokens, tool calls, test runs, wall time, wrong accepts, and final correctness.
- **Setup and migration.** `xpt init` and `xpt convert` are done.
- **Tracebacks outside pytest.** `xpt run` and `xpt.traceback.install()` are done.
- **Affected tests.** `pytest --xpt-map` and `xpt affected` are done.
- **MCP server.** `xpt mcp` and `xpt init --mcp` are done. Comparing it with the CLI in the agent benchmark is part of M5.
- **Environment report.** `xpt env` and `xpt env NAME` are done. A shadowing line in the pytest report when a test module fails to import is next.
- **Installed API listing.** `xpt api` is done.
- **Probing a line.** `xpt probe` is done.
- **M6. Release.** Name check, docs, PyPI.
