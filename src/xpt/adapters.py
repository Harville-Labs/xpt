"""Values from NumPy, PyTorch, pandas, Pydantic, attrs, and protobuf.

Each adapter is found by a class name in the value's MRO, so xpt never imports the library.
An adapter turns an object into a constructor call that rebuilds it.
"""

from __future__ import annotations

import keyword
import struct
import sys
from collections.abc import Callable
from typing import Any

from xpt.path import Attr, Key, Path
from xpt.unsupported import UnsupportedValue, type_name
from xpt.value import SPECIAL_FLOATS, Dataclass, Dict, Float, Int, Ref, Seq, Str, Value

LIMIT = 10_000

Recurse = Callable[[object, Path], Value]
Adapter = Callable[[Any, Path, Recurse], Value]

_PACK = {"float32": "f", "float16": "e"}


def find(cls: type) -> Adapter | None:
    for base in cls.__mro__:
        if adapter := _ADAPTERS.get(f"{base.__module__}.{base.__qualname__}"):
            return adapter
    if getattr(cls, "__attrs_attrs__", None) is not None:
        return _attrs
    return None


def same(a: object, b: object) -> bool | None:
    """Whether two values are equal, for types whose == does not return a bool."""
    check = _equality(type(a)) or _equality(type(b))
    if check is None:
        return None
    return type(a) is type(b) and check(a, b)


def _equality(cls: type) -> Callable[[Any, Any], bool] | None:
    for base in cls.__mro__:
        if check := _EQUALITY.get(f"{base.__module__}.{base.__qualname__}"):
            return check
    return None


def _ndarray(obj: Any, path: Path, recurse: Recurse) -> Value:
    dtype = obj.dtype
    if dtype.kind not in "biufU" or dtype.name == "float128":
        raise UnsupportedValue(path, f"is a numpy array of dtype {dtype.name}")
    name = dtype.str if dtype.kind == "U" else dtype.name
    return _array("numpy", obj.shape, obj.size, obj.tolist, Str(name), path, recurse)


def _tensor(obj: Any, path: Path, recurse: Recurse) -> Value:
    dtype = str(obj.dtype)
    if obj.is_complex():
        raise UnsupportedValue(path, f"is a torch tensor of dtype {dtype}")
    values = obj.detach().cpu().tolist
    ref = Ref(dtype.replace(".", ":", 1))
    return _array("torch", tuple(obj.shape), obj.numel(), values, ref, path, recurse)


def _array(
    module: str,
    shape: tuple[int, ...],
    size: int,
    values: Callable[[], object],
    dtype: Str | Ref,
    path: Path,
    recurse: Recurse,
) -> Value:
    if size > LIMIT:
        raise UnsupportedValue(
            path, f"is a {module} array of {size} elements, more than the {LIMIT} xpt writes inline"
        )
    if size == 0 and len(shape) > 1:
        dims = Seq("tuple", tuple(Int(n) for n in shape))
        return Dataclass(f"{module}:zeros", (("", dims), ("dtype", dtype)))
    pack = _PACK.get(dtype.v if isinstance(dtype, Str) else dtype.type.partition(":")[2])
    items = recurse(values(), path)
    if pack:
        items = _shorten(items, pack)
    ctor = "array" if module == "numpy" else "tensor"
    return Dataclass(f"{module}:{ctor}", (("", items), ("dtype", dtype)))


def _shorten(value: Value, pack: str) -> Value:
    match value:
        case Float(text) if text not in SPECIAL_FLOATS:
            return Float(_shortest(float(text), pack))
        case Seq(kind, items):
            return Seq(kind, tuple(_shorten(i, pack) for i in items))
    return value


def _shortest(x: float, pack: str) -> str:
    stored = struct.pack(pack, x)
    for digits in range(1, 17):
        text = repr(float(f"{x:.{digits}g}"))
        if struct.pack(pack, float(text)) == stored:
            return text
    return repr(x)


def _same_array(a: Any, b: Any) -> bool:
    return a.shape == b.shape and a.dtype == b.dtype and bool((a == b).all())


def _same_tensor(a: Any, b: Any) -> bool:
    return _same_array(a.detach().cpu(), b.detach().cpu())


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
    if obj.columns.nlevels > 1 or not obj.columns.is_unique:
        raise UnsupportedValue(path, "is a pandas DataFrame whose columns are not unique labels")
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
    fields = (("", Dict(tuple(columns))), *_index(obj, path, recurse))
    return Dataclass("pandas:DataFrame", fields)


def _index(obj: Any, path: Path, recurse: Recurse) -> tuple[tuple[str, Value], ...]:
    index = obj.index
    if (
        type(index).__name__ == "RangeIndex"
        and (index.start, index.step) == (0, 1)
        and index.name is None
    ):
        return ()
    if index.nlevels > 1 or index.name is not None:
        raise UnsupportedValue(path, "has a named or multi-level index")
    return (("index", recurse(index.tolist(), (*path, Attr("index")))),)


def _same_pandas(a: Any, b: Any) -> bool:
    if getattr(a, "ndim", 2) == 1 and (a.dtype != b.dtype or a.name != b.name):
        return False
    return bool(a.equals(b))


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
    for field, value in obj.ListFields():
        if field.is_extension:
            raise UnsupportedValue(path, f"sets the protobuf extension {field.full_name}")
        at = (*path, Attr(field.name))
        entry = field.message_type is not None and field.message_type.GetOptions().map_entry
        plain = dict(value) if entry else list(value) if field.is_repeated else value
        fields.append((_keyword(field.name, type(obj), path), recurse(plain, at)))
    return Dataclass(type_name(type(obj)), tuple(fields))


def _keyword(name: str, cls: type, path: Path) -> str:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise UnsupportedValue(
            path, f"is a {type_name(cls)} whose field {name!r} is not a keyword argument"
        )
    return name


_ADAPTERS: dict[str, Adapter] = {
    "numpy.ndarray": _ndarray,
    "numpy.generic": _scalar,
    "torch.Tensor": _tensor,
    "pandas.Series": _series,
    "pandas.DataFrame": _frame,
    "pydantic.main.BaseModel": _model,
    "google.protobuf.message.Message": _message,
}

_EQUALITY: dict[str, Callable[[Any, Any], bool]] = {
    "numpy.ndarray": _same_array,
    "torch.Tensor": _same_tensor,
    "pandas.Series": _same_pandas,
    "pandas.DataFrame": _same_pandas,
}
