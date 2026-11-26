# expect()

```python
from xpt import expect

expect(actual)  # missing: fails and records actual
expect(actual, expected)  # passes when actual equals expected, fails with a diff otherwise
expect(actual, expected, atol=1e-6)  # floats pass when within the tolerance
expect(actual, expected, ignore=['value["id"]'])  # leave volatile dict entries out
expect(actual, expected, unordered=True)  # lists compare as multisets
expect(actual, {1: 2, 2: 4}, key=n)  # one expected value per parametrized case
expect.output(capsys)  # captured stdout and stderr as lists of lines
expect.raises(lambda: parse("x"))  # missing: fails and records what parse raised
with expect.raises():  # the same for a block of statements
    parser.feed(data)
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
| Exceptions | Written as `ValueError("bad")`: the exact type and its `args`. One with `__notes__`, or one whose class can't be rebuilt by calling it with its `args`, is written as `expect.exception(ValueError, "bad", notes=["..."])`. See [Exceptions](#exceptions). |
| Pydantic models, attrs classes, protobuf messages | Written as a constructor call with keyword arguments: Pydantic by field alias, plus extra fields; attrs by `__init__` alias; protobuf by the fields that are set. A protobuf message that sets extensions is wrapped as `expect.with_extensions(msg, extensions={"pkg.name": value})`. |

Anything else fails with a message that names where the value is and its type:

```text
value["handler"] is a builtins:builtin_function_or_method; xpt cannot record it.
Register its class with xpt.register or expect a field of it instead.
```

For any other type, expect a field or a plain conversion of the value, for example `expect(str(path))` or `expect(dataclasses.asdict(obj))`.

### Your own classes

Register a class to record it as a keyword call to its constructor. Pass a function that returns the keyword arguments that rebuild an instance:

```python
import xpt

xpt.register(Money, lambda m: {"cents": m.cents, "currency": m.currency})

expect({"price": Money(250)}, {"price": Money(cents=250, currency="USD")})
```

Register at import time, in the module that defines the class or in `conftest.py`. The class must define `__eq__`, because xpt compares the actual value with the rebuilt expected one. `register` raises `TypeError` otherwise. Each key must be a valid keyword argument. A registration applies to that class only, not its subclasses, and takes precedence over how xpt would otherwise write it, so a dataclass can leave out a cache field.

xpt has no dependency on NumPy, PyTorch, pandas, Pydantic, attrs, or protobuf. It recognizes their types by class name and never imports them, so there is nothing extra to install.

Subclasses of builtins, such as an `IntEnum` member, are refused rather than recorded as their base type, because writing `1` where the code returns `Color.RED` would hide a real difference. A value that contains itself is refused and the message names the path to the cycle.

## Comparison

A call passes when `actual == expected`. Arrays, tensors, and pandas objects are the exception, because their `==` compares element by element: they pass only when both sides have the same type, shape, and dtype and every element is equal. A tensor must also be on the same device, and a pandas object must have an equal index, with the same names and dtype. `nan` equals `nan`, both as a plain float and as an array element in the same place, so a recorded `float("nan")` passes. Lists, tuples, dicts, and dataclasses that hold arrays are compared item by item the same way. When it fails, the diff and the grouping of repeated calls use the same structural equality, so `1` and `1.0` are equal. A dataclass is compared field by field, and its class must be the same.

### Tolerances

`atol` and `rtol` let floats differ a little. A float passes when `|actual - expected| <= atol + rtol * |expected|`, both are 0 by default, and both must be finite and 0 or more. The rule applies to every float in the value, to each element of a float array, tensor, or pandas column, and to the real and imaginary parts of a complex number on their own. Integers, integer arrays, strings, and everything else still compare exactly, so `expect(2, 1, atol=5)` fails. An integer next to a float uses the tolerance. An infinity equals only an infinity of the same sign, and `nan` equals `nan`. The failure header names the tolerance, as in `expect() mismatch (atol=0.001):`, and the diff lists only the values outside it. The tolerance is recorded with the run, so `xpt failures`, `xpt show`, and the end-of-session summary show the same diff, and `xpt accept` keeps the keywords in the source.

### Ignoring volatile fields

`ignore` takes a list of paths to dict entries that change from run to run, such as timestamps and generated ids. Each path is written the way a diff prints it, starting at `value`, so you can copy it from a failure:

```python
expect(create_user(), {"name": "ada"}, ignore=['value["id"]', 'value["created"]'])
expect(list_rows(), [{"v": 1}, {"v": 2}], ignore=['value[...]["id"]'])
expect(load(), Config(meta={"host": "a"}), ignore=['value.meta["pid"]'])
```

When the volatile value is a timestamp, a UUID, or a random choice, the [`xpt_deterministic`](pytest.md#deterministic-tests) fixture can pin it instead, so you can expect it rather than ignore it.

`[...]` matches every item of a list or tuple and every value of a dict. A path can pass through a dataclass field with `.name`, and a key can be a string, a number, a bool, or `None`. xpt removes the named entries from both sides before it compares, so the expected value may leave them out, and the value xpt writes when you accept leaves them out too. A path that names nothing in the value is fine; it ignores nothing.

Only dict entries can be ignored. A path that ends at a list item fails and suggests `unordered=True` or a key inside the items. A path that ends at a dataclass or model field fails too, because xpt must write every field to rebuild the object; expect `dataclasses.asdict(value)` or a dict of the fields you care about instead. These errors appear only when the call fails, since a call whose values are already equal returns before the paths are applied. A path xpt can't read, such as one not starting at `value` or one with a tuple key, raises `ValueError` and shows how to write one.

### Unordered lists

`unordered=True` compares every list and tuple in the value as a multiset: order is ignored, and repeated items must match in count. It applies at every depth, but not to the elements of an array, tensor, or pandas object, nor to a digest. Sets and dict keys already ignore order. Within a tolerance, an item matches any item within it. The diff lists unmatched items as `removed` and `added` at the list's path. xpt writes accepted values with every list sorted, so the source stays stable from run to run.

The failure header names every option in force, as in `expect() mismatch (atol=0.001, unordered, ignore=value["id"]):`. `ignore` and `unordered` are recorded with the run like the tolerance, so `xpt failures`, `xpt show`, and the end-of-session summary show the same diff, and `xpt accept` keeps the keywords in the source.

### Parametrized tests

A parametrized test that calls `expect` once runs one call site with many values. Without help, its cases disagree and xpt reports a conflict. Pass `key=` with the case's parameter, and give `expected` as a dict from each key to that case's expected value:

```python
@pytest.mark.parametrize("n", [1, 2, 3])
def test_double(n):
    expect(double(n), {1: 2, 2: 4, 3: 6}, key=n)
```

Each case compares against its own entry. A mismatch names the case in its path, as in `value[2]: 4 -> 5`, and a key with no entry fails with `expect() has no expected value for key=3`. Start with `expect(double(n), key=n)` and run `pytest --expect-create`; xpt fills the whole dict in one edit. Later, `xpt accept` writes the failing cases into the dict and keeps the passing ones. A new case alone counts as missing, so `--expect-create` adds it without touching the other entries. Entries for cases you delete stay in the dict until you remove them.

The key must be a value xpt can record and use as a dict key, such as a string, a number, or a tuple of them. `ignore` paths and `unordered` apply inside each case's value, so write `'value["id"]'`, not `'value[2]["id"]'`. Two calls with the same key and different values conflict, and so does a call site that mixes calls with and without `key=`.

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

## Exceptions

`expect.raises(fn)` calls `fn` with no arguments and records the exception it raises, so you don't have to guess its type or write a regex for its message:

```python
expect.raises(lambda: parse("x"))
# after xpt accept:
expect.raises(lambda: parse("x"), ValueError("invalid literal for int() with base 10: 'x'"))
```

For code that isn't one call, use the block form. The expected exception is its first argument:

```python
with expect.raises():
    parser.feed(data)
    parser.close()
# after xpt accept:
with expect.raises(ValueError("unexpected end of input")):
    parser.feed(data)
    parser.close()
```

The block form compares when the block ends and follows every rule below, including `subclass=` and `ignore=`. Write `expect.raises(...)` directly as the `with` item. A context manager made earlier and entered later still compares, but xpt can't tell where its expected exception goes, so the failure is `unlocatable`. When a `with` statement holds two `expect.raises()` items, the innermost one catches the exception, and the outer one sees none.

Two exceptions are equal when they have the same type, the same `args`, and the same `__notes__`. The traceback, `__cause__`, `__context__`, and other attributes are not compared or recorded. The type must match exactly. Pass `subclass=True` to let a subclass of the expected type match. When a subclass differs in its args, xpt records it under the expected type, so accepting keeps the type you wrote.

A failure names the part that differs, with `.args` as the path to the arguments:

```text
expect.raises() mismatch:
  value.args[0]: "old" -> "new"
  1 changed, 0 unchanged
```

`ignore=` takes paths the same way as for `expect()`, so it removes dict entries inside the arguments, such as `ignore=['value.args[0]["id"]']`. It can't remove an argument itself, because xpt must write every argument to rebuild the exception.

When `fn` returns instead of raising, the call fails with what it returned, `expect.raises(): parse("3") returned 3 instead of raising.`, and the failure has status `returned`. A block that finishes fails the same way, with `expect.raises(): the with block finished without raising.` and no value. It can't be accepted. Fix the code, or `expect()` the return value instead.

`expect.raises` catches every `Exception` and `SystemExit`. `KeyboardInterrupt`, `GeneratorExit`, and other `BaseException` subclasses pass through, unless the expected value is one of them. A failed `expect()` inside `fn` or the block always passes through, so it reports as itself.

xpt writes an exception as a call to its class with its `args`. It first checks that calling the class with those `args` gives back the same `args`. A class whose `__init__` takes other arguments, such as `json.JSONDecodeError`, fails that check, and so does any exception with notes. Those are written through `expect.exception`, which builds the instance without calling `__init__`:

```python
expect.raises(
    lambda: json.loads(""),
    expect.exception(JSONDecodeError, "Expecting value: line 1 column 1 (char 0)"),
)
expect.raises(load, expect.exception(ValueError, "bad", notes=["while reading app.toml"]))
```

An `ExceptionGroup` is written with its children, as `ExceptionGroup("many", [ValueError("a"), TypeError("b")])`, because they are part of its `args`. Every argument must itself be a value xpt can record.

The class is written by name under the same rules as a dataclass (see below), so a project exception needs an import in the test file. When the file has none, the accept is skipped and the reason names the import to add, such as `JSONDecodeError` from `json.decoder`.

`expect(error, ValueError("bad"))` compares a caught exception the same way, and an exception inside a dict or list is recorded the same way.

## Writing a dataclass, model, or array

xpt writes a dataclass by its class name, for example `Order(id=7, items=["apple"], total=12.5)`, but only when the test file can refer to it at module level. It uses the first of these that the file has:

1. `from shop import Order`, which writes `Order(...)`.
2. `import shop` or `import shop as s`, which writes `shop.Order(...)` or `s.Order(...)`.
3. Any other module-level binding of the name `Order`, such as a class statement or an assignment.

Arrays and tensors work the same way, so a file with `import numpy as np` gets `np.array(...)` and a file with `import torch` gets `torch.tensor(..., dtype=torch.float32)`. When the file has none of these, the accept is skipped and the reason names the import to add.

## Output

`expect.output` expects what a test printed, or what a subprocess wrote and returned:

```python
def test_build(capsys):
    main(["build", "--dry-run"])
    expect.output(capsys, {"stdout": ["would build 2 files"], "stderr": []})


def test_cli():
    result = subprocess.run(["mytool", "--version"], capture_output=True, text=True)
    expect.output(result, {"stdout": ["mytool 1.2"], "stderr": [], "returncode": 0})
```

The first argument is the `capsys` or `capfd` fixture, or a `subprocess.CompletedProcess`. Each stream is a list of lines, so a failure names the line that changed, as in `value["stdout"][3]: "built 2 files" -> "built 3 files"`. A final newline ends the last line rather than adding an empty one. A process also records its `returncode`, and a stream it did not capture, such as `stderr=subprocess.STDOUT`, is left out. Bytes are decoded as UTF-8, and a byte that is not valid UTF-8 is written as an escape such as `\xff`. Start with `expect.output(capsys)` and accept it like any other expectation; the dict goes in as the second argument.

Before comparing, xpt normalizes each line:

1. The project root, pytest's `rootdir` or the current directory without pytest, becomes `<root>`.
2. The temporary directory becomes `<tmp>`. A directory made by `tempfile.mkdtemp()` or pytest's `tmp_path` becomes `<tmp>` as a whole, so the random or numbered part does not change between runs.
3. Trailing whitespace is dropped, including the `\r` of a Windows line ending.

Both sides of the comparison see the normalized text, and xpt records it. When a rule changed the output of a failing call, the failure says so in a line such as `output normalized: the project root -> <root>, trailing whitespace dropped.` Pass `normalize=False` to compare the lines exactly as printed. The rules are fixed. For anything else that changes between runs, such as a timestamp, leave a whole stream out with `ignore=['value["stderr"]']`, or clean the text yourself and expect its lines with `expect(text.splitlines())`. `key=` works as it does for `expect`.

Reading `capsys` or `capfd` consumes what they captured, as `readouterr()` does, so that output is not in pytest's captured-output section when the test fails. The failure diff and the recorded actual value hold all of it.

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

`expect` works in plain `unittest` and in scripts. Without the pytest plugin, xpt records under the current directory and writes the run when the process exits. `XPT_NO_RECORD=1` turns recording off. `with xpt.deterministic.deterministic(test_id):` pins the clock, `random`, and `uuid` as the [`xpt_deterministic`](pytest.md#deterministic-tests) fixture does.
