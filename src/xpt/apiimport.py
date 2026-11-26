"""Read names from the imported module with `inspect`, for `xpt api --import`.

This runs the module's code. It is the fallback for what `ast` can't read: extension modules
without stubs, names bound at import time, and constructors inherited from outside the source.
"""

from __future__ import annotations

import importlib
import inspect
from types import ModuleType

from xpt import apimodel
from xpt.apimodel import Entry, Param, ParamKind, Signature

_KINDS: dict[inspect._ParameterKind, ParamKind] = {
    inspect.Parameter.POSITIONAL_ONLY: "positional_only",
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "positional",
    inspect.Parameter.VAR_POSITIONAL: "var_positional",
    inspect.Parameter.KEYWORD_ONLY: "keyword_only",
    inspect.Parameter.VAR_KEYWORD: "var_keyword",
}
_SIMPLE = (bool, int, float, complex, str, bytes, type(None))


def load(name: str) -> ModuleType | str:
    """The imported module, or why the import failed."""
    try:
        return importlib.import_module(name)
    except Exception as error:
        return f"importing {name} raised {type(error).__name__}: {error}"


def _annotation(value: object) -> str | None:
    if value is inspect.Parameter.empty:
        return None
    if isinstance(value, str):
        return value
    return inspect.formatannotation(value)


def _repr(value: object) -> str:
    try:
        return repr(value)
    except Exception:
        return "..."


def signature(obj: object) -> Signature | None:
    try:
        sig = inspect.signature(obj)  # ty: ignore[invalid-argument-type]
    except (TypeError, ValueError):
        return None
    params = tuple(
        Param(
            p.name,
            _KINDS[p.kind],
            _annotation(p.annotation),
            None if p.default is inspect.Parameter.empty else _repr(p.default),
        )
        for p in sig.parameters.values()
    )
    return Signature(params, _annotation(sig.return_annotation))


def _doc(obj: object) -> str:
    doc = getattr(obj, "__doc__", None)
    return apimodel.summary(doc) if isinstance(doc, str) else ""


def _belongs(obj: object, top: str) -> bool:
    owner = getattr(obj, "__module__", None)
    if not isinstance(owner, str):
        return True
    return owner.split(".")[0].lstrip("_") == top.lstrip("_")


def exported(module: ModuleType) -> list[str]:
    names = getattr(module, "__all__", None)
    if isinstance(names, list | tuple) and all(isinstance(n, str) for n in names):
        return list(names)
    top = module.__name__.split(".")[0]
    return [
        name
        for name, obj in vars(module).items()
        if not name.startswith("_") and not inspect.ismodule(obj) and _belongs(obj, top)
    ]


def entry(name: str, obj: object, module: str) -> Entry:
    if inspect.ismodule(obj):
        return Entry(name, "module", module, value=obj.__name__, doc=_doc(obj), resolved="import")
    if inspect.isclass(obj):
        sig = signature(obj)
        return Entry(
            name,
            "class",
            module,
            signatures=(sig,) if sig else (),
            known=sig is not None,
            doc=_doc(obj),
            resolved="import",
            members=tuple(
                m for key, raw in vars(obj).items() if (m := member(obj, key, raw)) is not None
            ),
        )
    if callable(obj):
        sig = signature(obj)
        return Entry(
            name,
            "function",
            module,
            signatures=(sig,) if sig else (),
            known=sig is not None,
            is_async=inspect.iscoroutinefunction(obj),
            doc=_doc(obj),
            resolved="import",
        )
    return Entry(name, "variable", module, value=_value(obj), resolved="import")


def _value(obj: object) -> str:
    if isinstance(obj, _SIMPLE):
        return " = " + apimodel.value(_repr(obj))
    return f": {type(obj).__qualname__}"


def member(cls: type, name: str, raw: object, public: bool = True) -> Entry | None:
    """A class attribute as a member entry, or None for a private name or a plain value."""
    if public and name.startswith("_"):
        return None
    module = getattr(cls, "__module__", "")
    if isinstance(raw, property):
        sig = signature(raw.fget) if raw.fget else None
        returns = sig.returns if sig else None
        return Entry(
            name, "property", module, value=returns or "", doc=_doc(raw), resolved="import"
        )
    static = isinstance(raw, staticmethod)
    try:
        bound = getattr(cls, name)
    except AttributeError:
        return None
    if not callable(bound) or inspect.isclass(bound):
        return None
    sig = signature(raw.__func__ if isinstance(raw, classmethod | staticmethod) else raw)
    if sig is None:
        sig = signature(bound)
    elif not static:
        sig = sig.without_first()
    return Entry(
        name,
        "method",
        module,
        signatures=(sig,) if sig else (),
        known=sig is not None,
        is_async=inspect.iscoroutinefunction(bound),
        doc=_doc(raw) or _doc(bound),
        resolved="import",
    )
