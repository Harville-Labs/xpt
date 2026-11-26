# Prior art notes

Read from source on 2026-11-19, and pytest-testmon on 2026-11-26. Paths are relative to each repository root.

| Project | Commit | License |
|---|---|---|
| pytorch/expecttest | 65d0976 | MIT |
| 15r10nk/inline-snapshot | 4542e42 | MIT |
| tophat/syrupy | 098dd11 | MIT |
| approvals/ApprovalTests.Python | 32ccbd1 | Apache-2.0 |
| tarpas/pytest-testmon | f536b09 | MIT |

## expecttest

- One 465-line module, `expecttest/__init__.py`, with no dependencies. Requires Python 3.8.1 or later.
- `assert_expected_inline(actual: str, expect: str, skip=0)` compares strings and prints a unified diff on failure.
- Setting `EXPECTTEST_ACCEPT=1` rewrites the file during the run. It finds the call through `traceback.extract_stack`, which gives a line number only. It then uses an `ast.walk` to widen that line to the full statement, and a regex to replace the first triple-quoted literal in the range (`RE_EXPECT`, `replace_string_literal`).
- An `EditHistory` adjusts line numbers after each in-process edit. It also catches one line accepted twice with different values ("Are you running a parametrized test?").
- It writes `<file>.bak` before the first edit to a file.
- Limitations:
  - Values must be strings.
  - The expectation must be a triple-quoted literal.
  - Two calls on one line are ambiguous.
  - Source is edited during the run.
  - Nothing persists after the process exits.

## inline-snapshot

- API: `snapshot()`, `external`, `outsource`, `Is`, `HasRepr`, `customize` hooks (`src/inline_snapshot/__init__.py:23-45`). It supports `==`, `<=`, `>=`, `in`, and `snapshot()[key]` (`docs/categories.md:31-39`).
- Finding the call: frame inspection plus `executing`, keyed on `(id(frame.f_code), frame.f_lasti)` (`_core/_inline_snapshot.py:33-50`, `_core/_types.py:18-19`).
- Rewriting: `Replacement(range, text)` edits applied through `asttokens` offsets, with a check that edits don't overlap (`_core/_rewrite_code.py:49-53,146-154,176-188`).
  - The whole file is reformatted only when it was already black-clean, or when `format-command` is set (`_rewrite_code.py:166-191`).
  - Black is an optional extra.
- Code generation:
  - A pluggy `customize(value, builder, local_vars, global_vars, snapshot_value)` hook (`_core/plugin/_spec.py:28-36`).
  - Many built-in types are handled (`_core/plugin/_default_plugin.py:33-290`).
  - Anything else falls back to `repr()`, wrapped in `HasRepr` when the repr does not parse (`_core/_customize/_builder.py:69-74`).
- Categories: create, fix, update, trim, and fix-assert (insider only).
  - Defaults are `create,review` in a terminal and `report` otherwise.
  - In CI it acts as `disable` (`pytest_plugin.py:175-177`, `_snapshot_session.py:50-73`).
- Review is one `Confirm.ask` per category, not per change (`_snapshot_session.py:103-118`).
- Changes are applied in `pytest_sessionfinish` of the same process (`pytest_plugin.py:200-241`). Proposed changes are not persisted.
- No stale-source detection was found. Offsets computed from the earlier parse are applied to the file re-read at write time (`_rewrite_code.py:156-188`).
- Failure output is the normal pytest assert diff, plus a unified source diff in rich panels. No JSON output was found.
- xdist: inline-snapshot turns itself off (`docs/limitations.md:6-16`).
- Runtime dependencies: asttokens, executing, rich, pytest, typing-extensions, and tomli before 3.11. Python 3.9 or later. Updates work on CPython only. No unittest support.
- Loops that produce different values fail even under `create`. The workaround is `Is()` or `snapshot()[key]` (`docs/eq_snapshot.md:266-288`).

## syrupy

- Snapshots are external `.ambr` files under `__snapshots__/`, sorted by name and written atomically (`extensions/amber/serializer.py:116-120,187-217`). "Syrupy does not support inline snapshots" (`README.md:587`).
- Extensions:
  - Whole-format extensions through `use_extension`.
  - Per-type serializer plugins (`serializer.py:81-95,366-370`).
  - Dict keys, sets, and dataclass fields are sorted. Cycles and a depth limit of 99 print as `...` (`serializer.py:113,341-345`).
- Matchers:
  - `path_type`, `path_value`, and `compose_matchers` (`matchers.py:38-116`).
  - Filters `paths`, `paths_include`, and `props` (`filters.py`).
  - No numeric tolerance was found.
- `--snapshot-update` rewrites snapshots during the run and deletes unused ones unless told not to (`session.py:463-511`). `--snapshot-update-new-only` exists. There is no review step and no deferred accept.
- Failure output is a line text diff with collapsed context. No JSON.
- xdist: `O_EXCL` lock files with a 60-second timeout. Worker reports are merged through `workeroutput` (`utils.py:124,222`, `session.py:301-344`).
- Depends on pytest only. Python 3.11 or later.

## ApprovalTests.Python

- `<name>.received.<ext>` is written next to `<name>.approved.<ext>`. A matching received file is deleted. A differing one stays (`file_approver.py:112-122`).
- Inline mode keeps the expected value in the test's docstring (`namer/inline_comparator.py:17-36`). On a mismatch it writes a full copy of the test file with the new docstring (`namer/inline_python_reporter.py:85-129`).
- Approving later without a rerun is possible. Failures are logged to `.approval_tests_temp/.failed_comparison.log`. `ReporterThatCreatesAnApprovalScript` appends `mv received approved` lines to a script (`reporters/reporter_that_creates_an_approval_script.py:22-41`).
  - There is no stale detection.
  - There are no per-change list, show, or reject commands.
- It downloads helper scripts from GitHub at runtime unless `APPROVALTESTS_DISABLE_SCRIPT_DOWNLOADS=1` is set (`log_commons.py:11-33`).
- Scrubbers are regex replacements to text (`scrubbers/scrubbers.py:12-76`). There are no path ignores and no tolerances.
- No xdist handling. No JSON output.

## pytest-testmon

Read for `xpt affected` (#70), which selects the tests that ran changed code.

- Depends on pytest and coverage.py (`pyproject.toml:29-32`). Lines are recorded by coverage.py with one dynamic context per test, `cov.switch_context(test_name)` (`testmon/testmon_core.py:715-725`), and read back in batches with `contexts_by_lineno` (`testmon_core.py:771-793`). It also tracks subprocesses through `COVERAGE_PROCESS_START` (`testmon_core.py:814-837`). It turns collection off under a debugger or a running coverage.py without pytest-cov (`testmon/configure.py:43-61`).
- Storage is one SQLite file, `.testmondata`, or `TESTMON_DATAFILE` (`testmon_core.py:50-56`), with a data version (`testmon/db.py:11`). An optional cloud store, testmon.net, replaces it (`testmon/pytest_testmon.py:110-133`).
- It fingerprints code, not lines. `Module.dump_and_block` turns each function body into a block and the module, with those bodies cut out, into one more block. Each block's checksum is the CRC32 of its `ast.dump` (`testmon/process_code.py:34-39,137-191`). A test keeps the checksums of the blocks it ran a line of (`process_code.py:298-313`), and it is affected when one of them is no longer in the file (`process_code.py:287-290`, `db.py:494-557`). So comments and formatting never select a test, and any change outside a function body selects every test that ran a line of the file. A module no test ran a line of, such as one that only holds constants, is never in a test's fingerprint.
- It compares the files on disk with the last run, not with a git ref. A file's sha is read from disk, and `git ls-files --stage` only saves reading files git reports unmodified (`process_code.py:87-102,238-279`, `testmon_core.py:507-523`). Every run with `--testmon` updates the database for the tests it ran, so the map stays current without a separate full run.
- It treats installed packages and the Python version as part of the environment. A changed package version reruns everything (`testmon_core.py:301-318`, `testmon/common.py:77-96`, `pytest_testmon.py:333-358`).
- Selection happens inside pytest. Unaffected files are skipped at collection and unaffected tests deselected (`pytest_testmon.py:535-560`). Tests that failed last time always run (`pytest_testmon.py:516-526`). Tests with no data run (`testmon_core.py:63-78`). Selected tests are ordered by duration. `-k`, `-m`, `--lf`, and naming a test turn selection off (`configure.py:64-87`).
- xdist: the controller decides and sends the selection to each worker in `workerinput`, and workers send their lines back on the teardown report (`pytest_testmon.py:407-430,451-483`).
- Output is one header line with the changed files and counts, and a savings line (`pytest_testmon.py:286-358,568-614`). No test is listed with its reason, there is no JSON, and the selection can't be printed without running it.

What xpt adds, and what it doesn't:

- **Adds.** No dependencies: the lines come from xpt's own tracer, shared with `--xpt-trace`. A diff against any git ref (`--since`), so it answers "what does this branch affect" and not only "what changed since the last run". Each selected test is printed with its reason, as text or JSON, before anything runs, so an agent can read the selection and pass it to any command. A map that can't be trusted is named as stale, and every test runs. A changed module that the map run imported but no test ran a line of runs every test, where testmon selects none.
- **Doesn't.** The map is built by a full run and replaced whole, not updated by each run. The granularity is the same as testmon's blocks, a function body or the code outside them, but xpt reads it from the diff's lines, so reformatting a line of code selects its tests where testmon's `ast.dump` checksum would not. Installed packages are not part of the map, so upgrading a dependency selects nothing. Subprocesses are not traced. Failing tests are not rerun unless they ran changed code.

## Prototype: call-site location without dependencies

`docs/prototypes/call_site.py` and `docs/prototypes/test_call_site_demo.py` were run on CPython 3.12 under pytest, with assertion rewriting on:

```bash
cd docs/prototypes && PYTHONPATH=. uv run --python 3.12 --with pytest pytest -p no:cacheprovider test_call_site_demo.py
```

Two tests fail on purpose, because they contain mismatches. Observations go to `observations.jsonl`. Results:

- `code.co_positions()[frame.f_lasti // 2]` matched exactly one `ast.Call` for every call. This held with two calls on one line, a multiline call with comments, a call after non-ASCII text on the same line, and a call inside a method in a loop.
- Splicing at UTF-8 byte offsets changed only the expected argument. Comments and the rest of each line were unchanged, and the result passed `compile()`.
- The loop recorded two observations at the same position with values `0` and `1`, so conflicts can be found by grouping records.
- Not yet covered: `-X no_debug_ranges`, decorators, `async def`, nested functions, lambdas, and calls through helpers.
