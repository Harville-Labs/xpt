# expect()

```python
from xpt import expect

expect(actual)  # missing: fails and records actual
expect(actual, expected)  # passes when actual equals expected, fails with a diff otherwise
```

`expect` compares `actual` with `expected`. When they differ, or when `expected` is left out, the test fails with `ExpectationFailed` and xpt records the actual value under `.xpt/` so `xpt accept` can write it into the source.

Pass `expected` as the second positional argument or as `expected=`. When accepting, xpt replaces the argument where it is.

## Values xpt can record

| Type | Notes |
|---|---|
| `str`, `int`, `float`, `bool`, `None` | `True` and `1` are different values. `nan`, `inf`, and `-0.0` round-trip. |
| `list`, `tuple`, `dict` | Order is kept. Dict keys can be any supported value. |
| `set`, `frozenset` | Members are sorted when written, so the source does not depend on hash order. |
| Dataclasses | Must compare by value (`eq=True`, the default) and have every field in `__init__`. |

Anything else fails with a message that names where the value is and its type:

```text
value["handler"] is a builtins:builtin_function_or_method; xpt cannot record it.
Register an adapter or expect a field of it instead.
```

Adapters for other types are on the roadmap and not yet available. Until then, expect a field or a plain conversion of the value, for example `expect(str(path))` or `expect(dataclasses.asdict(obj))`.

Subclasses of builtins, such as an `IntEnum` member, are refused rather than recorded as their base type, because writing `1` where the code returns `Color.RED` would hide a real difference. A value that contains itself is refused and the message names the path to the cycle.

## Comparison

A call passes when `actual == expected`. When it fails, the diff and the grouping of repeated calls use the same structural equality, so `1` and `1.0` are equal. A dataclass is compared field by field, and its class must be the same.

A record over 1 MB keeps its status but drops both values. Expect a smaller part of the value instead.

## Writing a dataclass

xpt writes a dataclass by its class name, for example `Order(id=7, items=["apple"], total=12.5)`, but only when the test file already binds that name at module level by an import, a class statement, or an assignment. Otherwise the accept is skipped and the reason names the missing import.

## Helpers

A helper that calls `expect` for its caller passes `_depth=1`. The failure then points at the caller's line, and accept writes the expected value into the caller's call:

```python
def check(actual, expected=None, *, label=""):
    expect(actual * 2, expected, _depth=1)


def test_it():
    check(21, label="half")
```

`xpt accept` turns the test's line into `check(21, 42, label="half")` and leaves the helper alone.

## Calls xpt can't rewrite

xpt finds a call by its function, its position among the `expect` calls in that function, and a hash of the expected argument's text. A failure is reported as `unlocatable`, and never accepted, when:

- the code has no source file, as with `exec`
- the file no longer parses
- the file changed after it was imported
- one line holds several calls and Python ran with `-X no_debug_ranges`

The comparison still runs in each case, so the test still fails or passes correctly.

## Without pytest

`expect` works in plain `unittest` and in scripts. Without the pytest plugin, xpt records under the current directory and writes the run when the process exits. `XPT_NO_RECORD=1` turns recording off.
