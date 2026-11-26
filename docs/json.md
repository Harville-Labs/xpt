# JSON output

Every `xpt` command takes `--json` and prints one JSON object on stdout. `pytest --xpt-json=PATH` writes the whole pytest report the same way. Each object has `"schema_version": 1`.

## Values

Values use xpt's recorded form. It is plain JSON where JSON can say the type, and a one-key object where it can't:

| Python | JSON |
|---|---|
| `{"a": [1, 2.5, True, None]}` | `{"dict": {"a": [1, 2.5, true, null]}}` |
| `(1,)` | `{"tuple": [1]}` |
| `{1: "x"}` | `{"dict": [[1, "x"]]}` |
| `float("nan")` | `{"float": "nan"}` |
| `Point(1, "a")` from module `values` | `{"values:Point": {"x": 1, "y": "a"}}` |
| `ValueError("bad", 3)` | `{"builtins:ValueError": {"*": {"tuple": ["bad", 3]}}}` |

`expected` is left out when there is none, so a recorded `None` stays distinct from a missing expectation.

## Group

`xpt failures --json` returns the run and its groups. Each group is one call site:

```json
{
  "schema_version": 1,
  "command": "failures",
  "run": "20261119T150800-b910",
  "groups": [
    {
      "id": "0eae",
      "site": {"path": "tests/test_demo.py", "line": 5, "qualname": "test_count"},
      "status": "mismatch",
      "acceptable": true,
      "rejected": false,
      "tests": ["tests/test_demo.py::test_count"],
      "detail": "",
      "actuals": [{"dict": {"status": "ok", "count": 4}}],
      "flaky": [],
      "expected": {"dict": {"status": "ok", "count": 3}},
      "changes": [
        {"path": "value[\"count\"]", "op": "replace", "expected": 3, "actual": 4}
      ],
      "unchanged": 1
    }
  ]
}
```

- `status` is one of `missing`, `mismatch`, `conflict`, `unsupported`, `returned`, or `unlocatable`.
- `acceptable` is true for `missing` and `mismatch`.
- `detail` says why a group can't be accepted.
- `actuals` holds the distinct actual values, one when every call agreed.
- `changes` and `unchanged` appear on a mismatch. `op` is `replace`, `add`, or `remove`.
- `flaky` lists the paths whose value changed between stored runs with the same source, empty when none did. See [Flaky](#flaky).

`xpt show ID --json` returns one group in the same form.

## Accept and reject

```json
{
  "schema_version": 1,
  "command": "accept",
  "run": "20261119T150800-b910",
  "dry_run": true,
  "outcomes": [
    {"id": "0eae", "site": {"path": "tests/test_demo.py", "line": 5, "qualname": "test_count"},
     "status": "accepted", "reason": ""}
  ],
  "changed": ["tests/test_demo.py"]
}
```

An accept outcome's `status` is `accepted`, `already_accepted`, `stale`, `skipped`, or `suspect`, and `reason` says why it wasn't written. A `suspect` reason names the path and the rule, as in `value["items"] is empty where the expected value had items (rule: empty).` or `value["created_at"] changed between 2 runs with no source change (rule: flaky).`

With `--verify`, the accept object also has `verify`:

```json
"verify": {
  "tests": ["tests/test_demo.py::test_count"],
  "exit_code": 0,
  "passed": true,
  "output": "1 passed in 0.03s.\n"
}
```

`tests` are the pytest node ids that were rerun. `exit_code` is pytest's. When nothing was written, `tests` is empty and `exit_code` and `passed` are null, because pytest did not run. `output` is pytest's combined stdout and stderr. `xpt reject --json` has the same `outcomes`, with `rejected`, `already_rejected`, or `skipped`, and no `changed`.

## Init

```json
{
  "schema_version": 1,
  "command": "init",
  "dry_run": false,
  "path": "AGENTS.md",
  "status": "created",
  "note": "",
  "checks": [
    {"name": "pytest", "ok": true, "detail": "pytest 9.0.1 in /project/.venv/bin/python"},
    {"name": "plugin", "ok": true, "detail": "pytest loads xpt's plugin. Configuration: pyproject.toml."}
  ],
  "mcp": {"path": ".mcp.json", "status": "added", "note": ""}
}
```

`status` is `created`, `added`, `present`, or `kept`. `note` says why a `kept` block was left alone, and is empty otherwise. `mcp` reports `.mcp.json` the same way with `--mcp`, and is null without it.

## Convert

```json
{
  "schema_version": 1,
  "command": "convert",
  "dry_run": false,
  "converted": [{"path": "tests/test_demo.py", "line": 4}],
  "skipped": [{"path": "tests/test_demo.py", "line": 9, "reason": "neither side is a literal."}],
  "changed": ["tests/test_demo.py"]
}
```

A skip's `line` is `null` when the whole file was skipped.

## Flaky

`xpt flaky --json` returns the stored runs and each call site with volatile paths. A group's `flaky` list has the same entries.

```json
{
  "schema_version": 1,
  "command": "flaky",
  "runs": ["20261119T150800-b910", "20261119T151200-c3d1"],
  "sites": [
    {
      "id": "7f3a",
      "site": {"path": "tests/test_order.py", "line": 7, "qualname": "test_create"},
      "run": "20261119T151200-c3d1",
      "flaky": [
        {"path": "value[\"created_at\"]", "kind": "timestamp", "values": 2,
         "runs": ["20261119T150800-b910", "20261119T151200-c3d1"], "spread": null,
         "suggestion": "ignore=['value[\"created_at\"]']"},
        {"path": "value[\"tags\"]", "kind": "order", "values": 2,
         "runs": ["20261119T150800-b910", "20261119T151200-c3d1"], "spread": null,
         "suggestion": "unordered=True"}
      ]
    }
  ]
}
```

- `run` is the newest run that recorded the call site, and the one its paths are judged in.
- `kind` is `timestamp`, `uuid`, `value`, `order`, or `float`. `values` counts the distinct values at the path. `runs` are the runs compared, which share the expected argument and the project source.
- `spread` is the largest difference between the values for a `float`, and null otherwise.
- `suggestion` is the `expect()` keyword that would keep the path stable, or null when none can, as for a path that is not a dict entry.

## Runs

```json
{
  "schema_version": 1,
  "command": "runs",
  "runs": [
    {"id": "20261119T150800-b910", "latest": true, "failures": 1}
  ]
}
```

## pytest `--xpt-json`

```json
{
  "schema_version": 1,
  "exit_code": 1,
  "summary": "1 failed in 0.04s. Next: xpt accept 0eae",
  "counts": ["1 failed"],
  "duration": 0.04,
  "run": "20261119T150800-b910",
  "created": null,
  "accepted": null,
  "failures": [
    {
      "test": "tests/test_demo.py::test_count",
      "when": "call",
      "path": "tests/test_demo.py",
      "line": 5,
      "function": null,
      "callers": [],
      "source": "expect({\"status\": \"ok\", \"count\": 4}, {\"status\": \"ok\", \"count\": 3})",
      "cause": null,
      "detail": {"kind": "expect", "observation": {"...": "..."}},
      "output": {},
      "traceback": "...",
      "group": {"id": "0eae", "...": "..."}
    }
  ]
}
```

- Nothing is truncated. The JSON has every change and the full traceback.
- `created` is null without `--expect-create`. With it, `created` holds `outcomes` and `changed` in the same form as `xpt accept --json`.
- `accepted` is null without `--expect-accept`. With it, `accepted` holds `outcomes` and `changed` in the same form, plus `unknown`, the named ids that matched no failure in the run.
- An expect failure adds `group` in the same form as `xpt failures --json`.
- A compared `assert` adds `changes` and `unchanged`, with sides named `left` and `right`.
