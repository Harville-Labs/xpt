# Quickstart

This walks through one loop: write a test with no expectation, let xpt fill it, change the code, and review the diff.

## 1. Install

```sh
uv add --dev xpt
```

If coding agents work in the project, `xpt init` adds xpt's instructions to `AGENTS.md` and checks that pytest loads the plugin. To move existing `assert value == literal` tests to `expect`, run `xpt convert tests --dry-run`, then without `--dry-run`.

## 2. Write a test without an expected value

```python title="tests/test_demo.py"
from xpt import expect


def summarize(items):
    return {"status": "ok", "count": len(items)}


def test_summary():
    expect(summarize(["a", "b", "c"]))
```

## 3. Fill it

```sh
pytest --expect-create
```

```text
FAILED tests/test_demo.py::test_summary  tests/test_demo.py:9
  > expect(summarize(["a", "b", "c"]))
  ee17  missing
  actual: {"status": "ok", "count": 3}
1 failed in 0.07s. Filled 1 missing expectation: tests/test_demo.py:9. Next: run the tests.
```

The run still fails, because the expectation was empty when the test ran. The file now reads:

```python
def test_summary():
    expect(summarize(["a", "b", "c"]), {"status": "ok", "count": 3})
```

Run `pytest` again and it passes. Read the new value before you commit it. `--expect-create` writes what the code returned, which is only right if the code is.

## 4. Change the code and review the diff

Say `summarize` starts counting one too many. The next `pytest` run shows only what changed:

```text
FAILED tests/test_demo.py::test_summary  tests/test_demo.py:9
  > expect(summarize(["a", "b", "c"]), {"status": "ok", "count": 3})
  0eae  mismatch
  value["count"]: 3 -> 4
  1 changed, 1 unchanged
1 failed in 0.04s. Next: xpt accept 0eae
```

Now decide.

- The new value is right: `xpt accept 0eae` writes it into the test.
- The new value is a bug: fix the code, or run `xpt reject 0eae` so xpt stops offering it for acceptance while you work on the fix.

`xpt accept` refuses if the expected argument changed since the run, so it never writes over an edit you made after the run.

## 5. Keep `.xpt/` out of Git

xpt records runs under `.xpt/` and writes `.xpt/.gitignore` the first time, so nothing there is committed. `xpt clean` deletes the directory.
