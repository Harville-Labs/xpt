# Python API

`xpt` exports `expect` and `register` (see [Your own classes](expect.md#your-own-classes)). `xpt.traceback.install()` turns on compact tracebacks when `XPT_TRACEBACK` is set (see [Tracebacks outside pytest](traceback.md)). Tools that want the CLI's behavior without parsing its output import `xpt.api`. The CLI is a thin wrapper over these functions.

```python
from pathlib import Path

from xpt import api

root = Path(".")
run = api.latest_run(root)
groups = api.mark(api.group(api.load_run(root, run)), api.load_rejections(root))
result = api.accept(root, [g for g in groups if g.offered], dry_run=True)
for outcome in result.outcomes:
    print(outcome.group.id, outcome.status, outcome.reason)
```

## Reading a run

| Function | Returns |
|---|---|
| `run_ids(root)` | Recorded run ids, oldest first. |
| `latest_run(root)` | The id of the newest complete run. Raises `RecordError` when there is none. |
| `load_run(root, run=None)` | The run's `Observation`s. `None` reads the latest run. Raises `RecordError` on a malformed record and names the file and line. |
| `group(observations)` | A tuple of `Group`, one per call site, sorted by path, line, and position. Pure. |

## Deciding

| Function | Effect |
|---|---|
| `accept(root, groups, *, dry_run=False, force=False)` | Writes each acceptable group's actual value into its source. Refuses a value that looks broken with the outcome `suspect` unless `force` is true. Returns `AcceptResult(outcomes, changed)`. Raises `AcceptError` when `.xpt/accept.lock` is held. |
| `reject(root, run, groups)` | Records each group's actual value as a bug. Returns a tuple of `RejectOutcome`. |
| `load_rejections(root)` | The recorded rejections, as a frozenset of `Rejection`. |
| `mark(groups, rejections)` | The groups with `rejected` set. Pure. |

## Comparing

| Function | Returns |
|---|---|
| `diff(expected, actual)` | `Diff(changes, unchanged)`, where each `Change` has `path`, `op`, `expected`, and `actual`. |
| `format_path(path, root="value")` | A change path as text, such as `value.items[1]`. |
| `to_json(value)`, `from_json(data)` | A `Value` to and from its [JSON form](json.md#values). |

## Types

| Type | Fields |
|---|---|
| `Observation` | `run`, `test`, `site`, `status`, `expected`, `actual`, `detail` |
| `Status` | `"missing"`, `"mismatch"`, `"unsupported"`, `"returned"` |
| `Group` | `id`, `site`, `status`, `actuals`, `expected`, `tests`, `detail`, `rejected`, and the properties `acceptable` and `offered` |
| `GroupStatus` | `Status` plus `"conflict"` and `"unlocatable"` |
| `CallSite` | `path`, `qualname`, `callee`, `ordinal`, `arg_sha`, `line` |
| `Unlocatable` | `reason`, `path`, `line` |
| `Outcome` | `group`, `status`, `reason` |
| `OutcomeStatus` | `"accepted"`, `"already_accepted"`, `"stale"`, `"skipped"`, `"suspect"` |
| `RejectOutcome` | `group`, `status`, `reason` |
| `RejectStatus` | `"rejected"`, `"already_rejected"`, `"skipped"` |
| `Value` | The recorded value tree. |

Every type is a frozen dataclass or a `Literal`. `offered` is true when a group is acceptable and not rejected, which is what `xpt accept --all` selects.

The API is pre-1.0. Names may change between minor versions.
