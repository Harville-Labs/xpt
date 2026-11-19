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
| NumPy arrays and scalars, PyTorch tensors | Written as `np.array([...], dtype="float32")` or `torch.tensor([...], dtype=torch.float32)`. Up to 10,000 elements. Complex dtypes are refused. A NumPy scalar is recorded as the Python number it holds. |
| pandas `Series` and `DataFrame` | Written as `pd.Series([...], dtype=..., name=..., index=[...])` and `pd.DataFrame({"col": [...]})`. A default index is left out. A named or multi-level index is refused, and so is a frame column whose dtype `pd.DataFrame` would not infer from its values. |
| Pydantic models, attrs classes, protobuf messages | Written as a constructor call with keyword arguments: Pydantic by field alias, plus extra fields; attrs by `__init__` alias; protobuf by the fields that are set. |

Anything else fails with a message that names where the value is and its type:

```text
value["handler"] is a builtins:builtin_function_or_method; xpt cannot record it.
Register an adapter or expect a field of it instead.
```

For any other type, expect a field or a plain conversion of the value, for example `expect(str(path))` or `expect(dataclasses.asdict(obj))`.

xpt has no dependency on NumPy, PyTorch, pandas, Pydantic, attrs, or protobuf. It recognizes their types by class name and never imports them, so there is nothing extra to install.

Subclasses of builtins, such as an `IntEnum` member, are refused rather than recorded as their base type, because writing `1` where the code returns `Color.RED` would hide a real difference. A value that contains itself is refused and the message names the path to the cycle.

## Comparison

A call passes when `actual == expected`. Arrays, tensors, and pandas objects are the exception, because their `==` compares element by element: they pass only when both sides have the same type, shape, and dtype and every element is equal, and a pandas object must also have an equal index. A `nan` element never equals itself, as with plain floats. Lists, tuples, dicts, and dataclasses that hold arrays are compared item by item the same way. Tolerances (`atol`, `rtol`) are not supported yet. When it fails, the diff and the grouping of repeated calls use the same structural equality, so `1` and `1.0` are equal. A dataclass is compared field by field, and its class must be the same.

A record over 1 MB keeps its status but drops both values. Expect a smaller part of the value instead.

## Writing a dataclass, model, or array

xpt writes a dataclass by its class name, for example `Order(id=7, items=["apple"], total=12.5)`, but only when the test file can refer to it at module level. It uses the first of these that the file has:

1. `from shop import Order`, which writes `Order(...)`.
2. `import shop` or `import shop as s`, which writes `shop.Order(...)` or `s.Order(...)`.
3. Any other module-level binding of the name `Order`, such as a class statement or an assignment.

Arrays and tensors work the same way, so a file with `import numpy as np` gets `np.array(...)` and a file with `import torch` gets `torch.tensor(..., dtype=torch.float32)`. When the file has none of these, the accept is skipped and the reason names the import to add.

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
