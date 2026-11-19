# xpt

xpt is an inline expect-test library for Python 3.11 and later. You write `expect(value)` in a test, run it, and xpt writes the value it saw into the test source when you accept it. It is built so coding agents can work with failing tests cheaply and safely, and so the people reviewing their work can see what changed.

```python
from xpt import expect


def test_parser():
    expect(parse("x + y"))
```

xpt has no runtime dependencies. It ships an `expect` function, a pytest plugin with a short report, and an `xpt` command for reviewing and accepting what a run recorded.

## Install

```sh
uv add --dev xpt
```

`pip install xpt` works too. The pytest plugin registers itself when xpt is installed.

## What a run looks like

A test with a wrong expectation and a test with none:

```python
def test_order():
    expect(make_order(), Order(7, ["apple"], 12.5))


def test_new():
    expect({"status": "ok", "count": 3})
```

`pytest` prints one short block per failure and a footer with the next command:

```text
FAILED tests/test_demo.py::test_order  tests/test_demo.py:18
  > expect(make_order(), Order(7, ["apple"], 12.5))
  b211  mismatch
  value.items[1]: added "pear"
  1 changed, 3 unchanged
FAILED tests/test_demo.py::test_new  tests/test_demo.py:22
  > expect({"status": "ok", "count": 3})
  ee17  missing
  actual: {"status": "ok", "count": 3}
2 failed in 0.08s. Next: xpt accept b211 ee17
```

`xpt accept b211 ee17` rewrites the two calls in place:

```python
def test_order():
    expect(make_order(), Order(id=7, items=["apple", "pear"], total=12.5))


def test_new():
    expect({"status": "ok", "count": 3}, {"status": "ok", "count": 3})
```

The next run passes.

## Where to go next

- [Quickstart](quickstart.md) walks through the loop from install to a passing run.
- [expect()](expect.md) covers what values xpt can record and compare.
- [Reviewing failures](review.md) covers `failures`, `show`, `accept`, `reject`, and `--expect-create`.
- [Coding agents](agents.md) has a snippet to paste into your `AGENTS.md`.
- [Design](design.md) explains why xpt works the way it does.

The site is also published as plain Markdown for language models. [llms.txt](llms.txt) indexes every page, and [llms-full.txt](llms-full.txt) holds all of them in one file. Each page is also available at its own path with a `.md` extension.
