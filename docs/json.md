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
      "expected": {"dict": {"status": "ok", "count": 3}},
      "changes": [
        {"path": "value[\"count\"]", "op": "replace", "expected": 3, "actual": 4}
      ],
      "unchanged": 1
    }
  ]
}
```

- `status` is one of `missing`, `mismatch`, `conflict`, `unsupported`, or `unlocatable`.
- `acceptable` is true for `missing` and `mismatch`.
- `detail` says why a group can't be accepted.
- `actuals` holds the distinct actual values, one when every call agreed.
- `changes` and `unchanged` appear on a mismatch. `op` is `replace`, `add`, or `remove`.

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

An accept outcome's `status` is `accepted`, `already_accepted`, `stale`, `skipped`, or `suspect`, and `reason` says why it wasn't written. A `suspect` reason names the path and the rule, as in `value["items"] is empty where the expected value had items (rule: empty).` `xpt reject --json` has the same `outcomes`, with `rejected`, `already_rejected`, or `skipped`, and no `changed`.

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
- An expect failure adds `group` in the same form as `xpt failures --json`.
- A compared `assert` adds `changes` and `unchanged`, with sides named `left` and `right`.
