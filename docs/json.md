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

`xpt show ID --json` returns one group in the same form, plus `changed_code`.

## Changed code

`xpt show --json` adds `changed_code` to the group, and `xpt accept --json` adds it to each outcome. See [Changed code](cli.md#changed-code).

```json
"changed_code": {
  "status": "ran",
  "since": "main",
  "functions": [
    {
      "path": "src/shop/price.py",
      "function": "apply_discount",
      "hunks": [
        {"line": 31, "count": 1, "removed": ["    rate = 0.1"], "added": ["    rate = 0.2"]}
      ]
    }
  ],
  "stale": [],
  "note": ""
}
```

- `status` is `ran` when the tests ran changed code, `none_ran` when they ran none, `no_trace` when the run was not traced with `pytest --xpt-trace`, and `no_git` when git could not diff.
- `since` is the ref diffed against, null for `no_trace` and `no_git`.
- `function` is the qualified name of the innermost function or class around the hunk, null at module level.
- A hunk's `line` and `count` are its new lines in the working tree. A removal has `count` 0 and `line` the line before it. `removed` and `added` are the lines as they are in the files, uncut.
- `stale` lists the files the tests ran that changed after the run, left out of `functions`.
- `note` is the git error for `no_git`, empty otherwise.

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

Each accept outcome also has [`changed_code`](#changed-code). An accept outcome's `status` is `accepted`, `already_accepted`, `stale`, `skipped`, or `suspect`, and `reason` says why it wasn't written. A `suspect` reason names the path and the rule, as in `value["items"] is empty where the expected value had items (rule: empty).` or `value["created_at"] changed between 2 runs with no source change (rule: flaky).`

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

## Env

`xpt env --json` returns the interpreter and every problem, each section a list that is empty when nothing is wrong.

```json
{
  "schema_version": 1,
  "command": "env",
  "interpreter": {
    "version": "3.12.7",
    "executable": ".venv/bin/python",
    "venv": ".venv",
    "lockfiles": ["uv.lock"],
    "matches_lock": false,
    "problems": []
  },
  "shadowing": [
    {"path": "json.py", "module": "json", "stdlib": true, "distributions": []}
  ],
  "drift": [
    {"name": "requests", "lockfile": "uv.lock", "locked": ["2.32.3"], "installed": "2.31.0"},
    {"name": "idna", "lockfile": "uv.lock", "locked": ["3.7"], "installed": null}
  ],
  "unreadable": []
}
```

- `venv` is null outside a virtual environment. `matches_lock` is null when there is no lockfile.
- `interpreter.problems` holds the `interpreter:` lines' text, and `unreadable` the `lockfile:` lines'.
- `distributions` names the installed distributions a shadowing module hides, and is empty for a standard library module.
- `locked` has more than one version when the lock pins a package differently per platform. `installed` is null for a missing package.

`xpt env NAME --json` returns the interpreter without the lock fields and the module:

```json
{
  "schema_version": 1,
  "command": "env",
  "interpreter": {"version": "3.12.7", "executable": ".venv/bin/python", "venv": ".venv", "problems": []},
  "module": {
    "name": "requests",
    "kind": "file",
    "origin": ".venv/lib/python3.12/site-packages/requests/__init__.py",
    "locations": [],
    "stdlib": false,
    "distribution": "requests",
    "version": "2.32.3",
    "shadows": null
  }
}
```

`kind` is `file`, `namespace` (with `locations` and no `origin`), `built-in`, `frozen`, or `missing`. `shadows` names what a project file hides, as in `the standard library module json`, and is null otherwise.

## Api

`xpt api --json` returns the same names as the text, under the same `--grep` and `--limit`, with each signature in full and its parameters one by one.

```json
{
  "schema_version": 1,
  "command": "api",
  "target": "json.dumps",
  "package": "json",
  "version": null,
  "stdlib": true,
  "python": "3.11.18",
  "modules": [
    {
      "name": "json",
      "doc": "",
      "entries": [
        {
          "name": "dumps",
          "kind": "function",
          "module": "json",
          "resolved": "static",
          "signatures": [
            {
              "text": "(obj, *, skipkeys=False, ..., sort_keys=False, **kw)",
              "params": [
                {"name": "obj", "kind": "positional", "annotation": null, "default": null},
                {"name": "skipkeys", "kind": "keyword_only", "annotation": null, "default": "False"}
              ],
              "returns": null
            }
          ],
          "doc": "Serialize `obj` to a JSON formatted `str`."
        }
      ]
    }
  ],
  "more": 0,
  "unresolved": 0,
  "notes": []
}
```

- `version` is the installed distribution's version, and null for the standard library or a module no distribution names.
- `modules` holds the target module and, with `--depth`, its submodules. `doc` is a submodule's first docstring sentence.
- `kind` is `function`, `class`, `method`, `property`, `variable`, `module`, or `unresolved`. `module` is where the name is defined, which differs from the section for a re-export.
- `resolved` is `static` when read from source or stubs, `import` when read with `--import`, and `none` for an unresolved name, whose `reason` says why.
- `signatures` holds one item per overload, and is null when the constructor can't be read statically. A parameter's `kind` is `positional_only`, `positional`, `var_positional`, `keyword_only`, or `var_keyword`. Annotations and defaults are source text. `async` is true for a coroutine function.
- `value` is a variable's annotation and value as text, a property's type, or a module entry's dotted name. `members` lists a class's methods, properties, and enum members.
- `more` counts the names `--limit` left out. `notes` holds the text lines that follow the names.

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
