"""Values from NumPy, PyTorch, pandas, Pydantic, attrs, and protobuf.

Each adapter is found by a class name in the value's MRO, so xpt never imports the library.
An adapter turns an object into a constructor call that rebuilds it.
"""

from __future__ import annotations

import keyword
import struct
import sys
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, TypeVar

from xpt import digest
from xpt.digest import Digest
from xpt.path import Attr, Key, Path
from xpt.tolerance import Tolerance
from xpt.unsupported import UnsupportedValue, type_name
from xpt.value import SPECIAL_FLOATS, Dataclass, Dict, Float, Int, Ref, Seq, Str, Value

LIMIT = 10_000

Recurse = Callable[[object, Path], Value]
Adapter = Callable[[Any, Path, Recurse], Value]

T = TypeVar("T")

_PACK = {"float32": "f", "float16": "e", "complex64": "f", "complex32": "e"}


def register(cls: type[T], fields: Callable[[T], Mapping[str, object]]) -> None:
    """Record instances of `cls` as `cls(**fields(obj))` and compare them with `cls.__eq__`.

    A registration takes precedence over xpt's own handling of the class, applies to `cls`
    alone and not its subclasses, and replaces an earlier registration of the same class.
    """
    if cls.__eq__ is object.__eq__:
        raise TypeError(
            f"{cls.__module__}.{cls.__qualname__} has no __eq__, so a rebuilt one never equals "
            "the original. Define __eq__ before registering it."
        )
    _REGISTERED[cls] = fields


def find(cls: type) -> Adapter | None:
    if (fields := _REGISTERED.get(cls)) is not None:
        return partial(_registered, fields)
    for base in cls.__mro__:
        if adapter := _ADAPTERS.get(f"{base.__module__}.{base.__qualname__}"):
            return adapter
    if getattr(cls, "__attrs_attrs__", None) is not None:
        return _attrs
    return None


def same(a: object, b: object, tol: Tolerance) -> bool | None:
    """Whether two values are equal, for types whose == does not return a bool."""
    check = _equality(type(a)) or _equality(type(b))
    if check is None:
        return None
    return type(a) is type(b) and check(a, b, tol)


def _equality(cls: type) -> Callable[[Any, Any, Tolerance], bool] | None:
    for base in cls.__mro__:
        if check := _EQUALITY.get(f"{base.__module__}.{base.__qualname__}"):
            return check
    return None


def _ndarray(obj: Any, path: Path, recurse: Recurse) -> Value:
    dtype = obj.dtype
    if dtype.kind not in "biufcU" or dtype.name in ("float128", "complex256"):
        raise UnsupportedValue(path, f"is a numpy array of dtype {dtype.name}")
    if obj.size > LIMIT:
        return _digest(digest.of_numpy(obj), path, recurse)
    name = dtype.str if dtype.kind == "U" else dtype.name
    return _array("numpy", obj.shape, obj.size, obj.tolist, Str(name), (), path, recurse)


def _tensor(obj: Any, path: Path, recurse: Recurse) -> Value:
    if obj.numel() > LIMIT:
        return _digest(digest.of_torch(obj), path, recurse)
    values = obj.detach().cpu().tolist
    ref = _torch_dtype(obj.dtype)
    device = () if obj.device.type == "cpu" else (("device", Str(str(obj.device))),)
    return _array("torch", tuple(obj.shape), obj.numel(), values, ref, device, path, recurse)


def _array(
    module: str,
    shape: tuple[int, ...],
    size: int,
    values: Callable[[], object],
    dtype: Str | Ref,
    extra: tuple[tuple[str, Value], ...],
    path: Path,
    recurse: Recurse,
) -> Value:
    if size == 0 and len(shape) > 1:
        dims = Seq("tuple", tuple(Int(n) for n in shape))
        return Dataclass(f"{module}:zeros", (("", dims), ("dtype", dtype), *extra))
    pack = _PACK.get(dtype.v if isinstance(dtype, Str) else dtype.type.partition(":")[2])
    items = recurse(values(), path)
    if pack:
        items = _shorten(items, pack)
    ctor = "array" if module == "numpy" else "tensor"
    return Dataclass(f"{module}:{ctor}", (("", items), ("dtype", dtype), *extra))


def _torch_dtype(dtype: object) -> Ref:
    return Ref(str(dtype).replace(".", ":", 1))


def _digest(d: Digest, path: Path, recurse: Recurse) -> Value:
    dtype = _torch_dtype(d.dtype) if d.kind == "torch" else Str(d.dtype)
    shape = Seq("tuple", tuple(Int(n) for n in d.shape))
    fields: list[tuple[str, Value]] = [("kind", Str(d.kind)), ("shape", shape), ("dtype", dtype)]
    if d.device:
        fields.append(("device", Str(d.device)))
    fields.append(("sha256", Str(d.sha256)))
    for name in ("min", "max", "mean"):
        if (stat := getattr(d, name)) is not None:
            fields.append((name, recurse(stat, (*path, Attr(name)))))
    return Dataclass("xpt:expect.digest", tuple(fields))


def _shorten(value: Value, pack: str) -> Value:
    match value:
        case Float(text) if text not in SPECIAL_FLOATS:
            return Float(_shortest(float(text), pack))
        case Seq(kind, items):
            return Seq(kind, tuple(_shorten(i, pack) for i in items))
        case Dataclass("builtins:complex", parts):
            return Dataclass("builtins:complex", tuple((n, _shorten(v, pack)) for n, v in parts))
    return value


def _shortest(x: float, pack: str) -> str:
    stored = struct.pack(pack, x)
    for digits in range(1, 17):
        text = repr(float(f"{x:.{digits}g}"))
        if struct.pack(pack, float(text)) == stored:
            return text
    return repr(x)


def _same_numpy(a: Any, b: Any, tol: Tolerance) -> bool:
    return _same_array(a, b, tol, a.dtype.kind in "fc", a.dtype.kind == "c")


def _same_tensor(a: Any, b: Any, tol: Tolerance) -> bool:
    if a.device != b.device:
        return False
    complex_ = a.is_complex()
    inexact = complex_ or a.is_floating_point()
    return _same_array(a.detach().cpu(), b.detach().cpu(), tol, inexact, complex_)


def _same_array(a: Any, b: Any, tol: Tolerance, inexact: bool, complex_: bool) -> bool:
    if a.shape != b.shape or a.dtype != b.dtype:
        return False
    if not (tol and inexact):
        return bool(((a == b) | ((a != a) & (b != b))).all())
    if complex_:
        return bool((tol.within(a.real, b.real) & tol.within(a.imag, b.imag)).all())
    return bool(tol.within(a, b).all())


def _scalar(obj: Any, path: Path, recurse: Recurse) -> Value:
    return recurse(obj.item(), path)


def _series(obj: Any, path: Path, recurse: Recurse) -> Value:
    _limit("pandas Series", len(obj), path)
    fields = [("", recurse(obj.tolist(), path)), ("dtype", Str(str(obj.dtype)))]
    if obj.name is not None:
        fields.append(("name", recurse(obj.name, (*path, Attr("name")))))
    return Dataclass("pandas:Series", (*fields, *_index(obj, path, recurse)))


def _frame(obj: Any, path: Path, recurse: Recurse) -> Value:
    _limit("pandas DataFrame", obj.size, path)
    if not obj.columns.is_unique:
        raise UnsupportedValue(path, "is a pandas DataFrame whose column labels repeat")
    pd = sys.modules["pandas"]
    columns = []
    for label in obj.columns:
        column = obj[label]
        values = column.tolist()
        if pd.Series(values).dtype != column.dtype:
            raise UnsupportedValue(
                path,
                f"is a pandas DataFrame whose column {label!r} has dtype {column.dtype}, which "
                "pd.DataFrame would not rebuild from its values. Expect that column as a Series",
            )
        key = recurse(label, path)
        columns.append((key, recurse(values, (*path, Key(key)))))
    fields = [("", Dict(tuple(columns))), *_index(obj, path, recurse)]
    if any(name is not None for name in obj.columns.names):
        fields.append(("columns", _axis(obj.columns, (*path, Attr("columns")), recurse)))
    return Dataclass("pandas:DataFrame", tuple(fields))


def _index(obj: Any, path: Path, recurse: Recurse) -> tuple[tuple[str, Value], ...]:
    index = obj.index
    if (
        type(index).__name__ == "RangeIndex"
        and (index.start, index.step) == (0, 1)
        and index.name is None
    ):
        return ()
    return (("index", _axis(index, (*path, Attr("index")), recurse)),)


def _axis(index: Any, path: Path, recurse: Recurse) -> Value:
    names = list(index.names)
    if index.nlevels > 1:
        fields = [("", recurse(list(index), path))]
        if any(name is not None for name in names):
            fields.append(("names", recurse(names, (*path, Attr("names")))))
        return Dataclass("pandas:MultiIndex.from_tuples", tuple(fields))
    values = index.tolist()
    inferred = sys.modules["pandas"].Index(values).dtype == index.dtype
    if inferred and index.name is None:
        return recurse(values, path)
    fields = [("", recurse(values, path))]
    if not inferred:
        fields.append(("dtype", Str(str(index.dtype))))
    if index.name is not None:
        fields.append(("name", recurse(index.name, (*path, Attr("name")))))
    return Dataclass("pandas:Index", tuple(fields))


def _same_pandas(a: Any, b: Any, tol: Tolerance) -> bool:
    if a.ndim == 1 and (a.dtype != b.dtype or a.name != b.name):
        return False
    axes = [(a.index, b.index)]
    if a.ndim == 2:
        axes.append((a.columns, b.columns))
    for mine, theirs in axes:
        if list(mine.names) != list(theirs.names) or mine.dtype != theirs.dtype:
            return False
    if not tol:
        return bool(a.equals(b))
    if not all(mine.equals(theirs) for mine, theirs in axes):
        return False
    if a.ndim == 1:
        return _same_column(a, b, tol)
    return all(_same_column(a.iloc[:, i], b.iloc[:, i], tol) for i in range(a.shape[1]))


def _same_column(a: Any, b: Any, tol: Tolerance) -> bool:
    if a.dtype != b.dtype:
        return False
    kind = a.dtype.kind
    if not type(a.dtype).__module__.startswith("numpy") or kind not in "fc":
        return bool(a.equals(b))
    return _same_array(a.to_numpy(), b.to_numpy(), tol, True, kind == "c")


def _limit(kind: str, size: int, path: Path) -> None:
    if size > LIMIT:
        raise UnsupportedValue(
            path, f"is a {kind} of {size} elements, more than the {LIMIT} xpt writes inline"
        )


def _model(obj: Any, path: Path, recurse: Recurse) -> Value:
    cls = type(obj)
    fields = []
    for name, info in cls.model_fields.items():
        alias = info.validation_alias if isinstance(info.validation_alias, str) else info.alias
        value = recurse(getattr(obj, name), (*path, Attr(name)))
        fields.append((_keyword(alias or name, cls, path), value))
    for name, extra in (obj.model_extra or {}).items():
        fields.append((_keyword(name, cls, path), recurse(extra, (*path, Attr(name)))))
    return Dataclass(type_name(cls), tuple(fields))


def _attrs(obj: Any, path: Path, recurse: Recurse) -> Value:
    cls = type(obj)
    if cls.__eq__ is object.__eq__:
        raise UnsupportedValue(path, f"is a {type_name(cls)} with eq=False, so == is identity")
    fields = []
    for a in cls.__attrs_attrs__:
        if not a.init or not a.eq:
            raise UnsupportedValue(
                path, f"is a {type_name(cls)} whose field {a.name!r} has init or eq off"
            )
        value = recurse(getattr(obj, a.name), (*path, Attr(a.name)))
        fields.append((_keyword(a.alias or a.name, cls, path), value))
    return Dataclass(type_name(cls), tuple(fields))


def _message(obj: Any, path: Path, recurse: Recurse) -> Value:
    fields = []
    extensions = []
    for field, value in obj.ListFields():
        entry = field.message_type is not None and field.message_type.GetOptions().map_entry
        plain = dict(value) if entry else list(value) if field.is_repeated else value
        if field.is_extension:
            key = Str(field.full_name)
            extensions.append((key, recurse(plain, (*path, Attr("extensions"), Key(key)))))
        else:
            at = (*path, Attr(field.name))
            fields.append((_keyword(field.name, type(obj), path), recurse(plain, at)))
    message = Dataclass(type_name(type(obj)), tuple(fields))
    if not extensions:
        return message
    return Dataclass(
        "xpt:expect.with_extensions", (("", message), ("extensions", Dict(tuple(extensions))))
    )


def _registered(fields: Callable[[Any], object], obj: Any, path: Path, recurse: Recurse) -> Value:
    cls = type(obj)
    found = fields(obj)
    if not isinstance(found, Mapping):
        raise UnsupportedValue(
            path,
            f"is a {type_name(cls)} whose registered fields returned a "
            f"{type(found).__name__}, not a dict",
        )
    return Dataclass(
        type_name(cls),
        tuple(
            (_keyword(name, cls, path), recurse(value, (*path, Attr(name))))
            for name, value in found.items()
        ),
    )


def _keyword(name: object, cls: type, path: Path) -> str:
    if not (isinstance(name, str) and name.isidentifier()) or keyword.iskeyword(name):
        raise UnsupportedValue(
            path, f"is a {type_name(cls)} whose field {name!r} is not a keyword argument"
        )
    return name


_REGISTERED: dict[type, Callable[[Any], object]] = {}

_ADAPTERS: dict[str, Adapter] = {
    "numpy.ndarray": _ndarray,
    "numpy.generic": _scalar,
    "torch.Tensor": _tensor,
    "pandas.Series": _series,
    "pandas.DataFrame": _frame,
    "pydantic.main.BaseModel": _model,
    "google.protobuf.message.Message": _message,
    "xpt.digest.Digest": _digest,
}

_EQUALITY: dict[str, Callable[[Any, Any, Tolerance], bool]] = {
    "numpy.ndarray": _same_numpy,
    "torch.Tensor": _same_tensor,
    "pandas.Series": _same_pandas,
    "pandas.DataFrame": _same_pandas,
}
