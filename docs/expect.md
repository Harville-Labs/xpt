# expect()

```python
from xpt import expect

expect(actual)  # missing: fails and records actual
expect(actual, expected)  # passes when actual equals expected, fails with a diff otherwise
expect(actual, expected, atol=1e-6)  # floats pass when within the tolerance
```

`expect` compares `actual` with `expected`. When they differ, or when `expected` is left out, the test fails with `ExpectationFailed` and xpt records the actual value under `.xpt/` so `xpt accept` can write it into the source.

Pass `expected` as the second positional argument or as `expected=`. When accepting, xpt replaces the argument where it is.

## Values xpt can record

| Type | Notes |
|---|---|
| `str`, `int`, `float`, `bool`, `None` | `True` and `1` are different values. `nan`, `inf`, and `-0.0` round-trip. |
| `complex` | Written as `complex(real=1.0, imag=2.0)`. |
| `list`, `tuple`, `dict` | Order is kept. Dict keys can be any supported value. |
| `set`, `frozenset` | Members are sorted when written, so the source does not depend on hash order. |
| Dataclasses | Must compare by value (`eq=True`, the default) and have every field in `__init__`. |
| NumPy arrays and scalars, PyTorch tensors | Written as `np.array([...], dtype="float32")` or `torch.tensor([...], dtype=torch.float32)`. An array over 10,000 elements is written as a digest instead (see [Large arrays](#large-arrays)). Complex dtypes are written with `complex(...)` elements. A tensor on a device other than the CPU adds `device="mps:0"`. A NumPy scalar is recorded as the Python number it holds. |
| pandas `Series` and `DataFrame` | Written as `pd.Series([...], dtype=..., name=..., index=[...])` and `pd.DataFrame({"col": [...]})`. A default index is left out. A named index, or one whose dtype pandas would not infer, is written as `pd.Index([...], dtype=..., name=...)`, and a multi-level one as `pd.MultiIndex.from_tuples([...], names=[...])`. Multi-level columns are tuple keys. A frame column whose dtype `pd.DataFrame` would not infer from its values is refused. |
| Pydantic models, attrs classes, protobuf messages | Written as a constructor call with keyword arguments: Pydantic by field alias, plus extra fields; attrs by `__init__` alias; protobuf by the fields that are set. A protobuf message that sets extensions is wrapped as `expect.with_extensions(msg, extensions={"pkg.name": value})`. |

Anything else fails with a message that names where the value is and its type:

```text
value["handler"] is a builtins:builtin_function_or_method; xpt cannot record it.
Register an adapter or expect a field of it instead.
```

For any other type, expect a field or a plain conversion of the value, for example `expect(str(path))` or `expect(dataclasses.asdict(obj))`.

xpt has no dependency on NumPy, PyTorch, pandas, Pydantic, attrs, or protobuf. It recognizes their types by class name and never imports them, so there is nothing extra to install.

Subclasses of builtins, such as an `IntEnum` member, are refused rather than recorded as their base type, because writing `1` where the code returns `Color.RED` would hide a real difference. A value that contains itself is refused and the message names the path to the cycle.

## Comparison

A call passes when `actual == expected`. Arrays, tensors, and pandas objects are the exception, because their `==` compares element by element: they pass only when both sides have the same type, shape, and dtype and every element is equal. A tensor must also be on the same device, and a pandas object must have an equal index, with the same names and dtype. `nan` equals `nan`, both as a plain float and as an array element in the same place, so a recorded `float("nan")` passes. Lists, tuples, dicts, and dataclasses that hold arrays are compared item by item the same way. When it fails, the diff and the grouping of repeated calls use the same structural equality, so `1` and `1.0` are equal. A dataclass is compared field by field, and its class must be the same.

### Tolerances

`atol` and `rtol` let floats differ a little. A float passes when `|actual - expected| <= atol + rtol * |expected|`, both are 0 by default, and both must be finite and 0 or more. The rule applies to every float in the value, to each element of a float array, tensor, or pandas column, and to the real and imaginary parts of a complex number on their own. Integers, integer arrays, strings, and everything else still compare exactly, so `expect(2, 1, atol=5)` fails. An integer next to a float uses the tolerance. An infinity equals only an infinity of the same sign, and `nan` equals `nan`. The failure header names the tolerance, as in `expect() mismatch (atol=0.001):`, and the diff lists only the values outside it. The tolerance is recorded with the run, so `xpt failures`, `xpt show`, and the end-of-session summary show the same diff, and `xpt accept` keeps the keywords in the source.

### Large arrays

An array or tensor over 10,000 elements is too long to read inline, so xpt writes a digest of it:

```python
expect(
    image,
    expect.digest(
        kind="numpy",
        shape=(250, 200),
        dtype="float64",
        sha256="a9b30e873fa795aa986ad625c0d5d324557b71280a09c3b048dd2bb889a3ea87",
        min=0.0,
        max=1.0,
        mean=0.5,
    ),
)
```

The digest equals an array of the same kind, shape, dtype, and device whose bytes hash to `sha256`. The hash covers the elements in C order and little-endian byte order, so `-0.0` and `0.0` differ and `atol` and `rtol` do not apply. `min`, `max`, and `mean` are written for numeric arrays so a reviewer can see what changed, and they are never compared. When the data changes, the diff shows the new hash and the new `min`, `max`, and `mean`.

### Array failures

When more than one element of an array or tensor differs, the failure starts with a count and the largest difference, then lists the first changes by index:

```
expect() mismatch:
  value: 15 of 1200 elements differ, the largest by 0.25 at value[3][1]
  value[3][1]: 0.0 -> 0.25
  value[5][0]: 0.0 -> 0.01
  ...
```

The count needs the same shape on both sides. The largest difference is measured on numbers, so a bool or string array gets the count alone.

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
