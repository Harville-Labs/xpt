"""The shapes `xpt api` lists, and how a signature is shortened to one line."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal[
    "function",
    "class",
    "method",
    "property",
    "variable",
    "module",
    "unresolved",
]
ParamKind = Literal[
    "positional_only", "positional", "var_positional", "keyword_only", "var_keyword"
]
Resolved = Literal["static", "import", "none"]

PARAMS_WIDTH = 80
DEFAULT_WIDTH = 20
ANNOTATION_WIDTH = 40
VALUE_WIDTH = 40
DOC_WIDTH = 72

_ECHO = re.compile(r"[\w.]+\(.*\)(\s*->.*)?$")
_QUALIFIED = re.compile(r"\b(?:typing_extensions|typing|collections\.abc|_typeshed)\.")


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    kind: ParamKind
    annotation: str | None = None
    default: str | None = None


@dataclass(frozen=True, slots=True)
class Signature:
    params: tuple[Param, ...]
    returns: str | None = None

    def without_first(self) -> Signature:
        first = self.params[:1]
        if first and first[0].kind in ("positional_only", "positional"):
            return Signature(self.params[1:], self.returns)
        return self


@dataclass(frozen=True, slots=True)
class Entry:
    """One public name: its kind, its signatures (several for overloads), and what it holds."""

    name: str
    kind: Kind
    module: str
    signatures: tuple[Signature, ...] = ()
    known: bool = True
    is_async: bool = False
    value: str = ""
    doc: str = ""
    resolved: Resolved = "static"
    reason: str = ""
    members: tuple[Entry, ...] = field(default=())


def unresolved(name: str, module: str, reason: str) -> Entry:
    return Entry(name, "unresolved", module, known=False, resolved="none", reason=reason)


def summary(doc: str | None) -> str:
    """The first sentence of a docstring's first paragraph, cut to DOC_WIDTH."""
    if not doc:
        return ""
    text = inspect.cleandoc(doc).strip()
    while _ECHO.match(text.partition("\n")[0]):
        text = text.partition("\n")[2].strip()
    paragraph = text.split("\n\n")[0]
    text = " ".join(paragraph.split()).replace("``", "`")
    end = text.find(". ")
    if end != -1:
        text = text[: end + 1]
    if len(text) <= DOC_WIDTH:
        return text
    return text[: DOC_WIDTH - 3].rsplit(" ", 1)[0] + "..."


def annotation(text: str, short: bool = True) -> str:
    """An annotation without typing prefixes, with long subscripts folded to `[...]`."""
    text = _QUALIFIED.sub("", text)
    if not short or len(text) <= ANNOTATION_WIDTH:
        return text
    parts = [_fold(part) for part in _split_union(text)]
    folded = " | ".join(parts)
    if len(folded) <= ANNOTATION_WIDTH:
        return folded
    return folded[: ANNOTATION_WIDTH - 3] + "..."


def _fold(part: str) -> str:
    bracket = part.find("[")
    return part if bracket == -1 or len(part) <= ANNOTATION_WIDTH // 2 else part[:bracket] + "[...]"


def _split_union(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, char in enumerate(text):
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    return [*parts, text[start:].strip()]


def default(text: str, short: bool = True) -> str:
    if short and (len(text) > DEFAULT_WIDTH or text.startswith("<")):
        return "..."
    return text


def value(text: str, short: bool = True) -> str:
    if short and len(text) > VALUE_WIDTH:
        return text[: VALUE_WIDTH - 3] + "..."
    return text


def _param_text(p: Param, short: bool) -> str:
    star = {"var_positional": "*", "var_keyword": "**"}.get(p.kind, "")
    text = star + p.name
    if p.annotation is not None:
        text += ": " + annotation(p.annotation, short)
    if p.default is not None:
        shown = default(p.default, short)
        text += (" = " if p.annotation is not None else "=") + shown
    return text


def signature_text(sig: Signature, short: bool = True) -> str:
    """`(a, b=1, *, c=...) -> R`.

    Past PARAMS_WIDTH the short form drops optional parameters, leaving one `...`, and the
    annotations of required ones, which it always keeps.
    """
    out: list[str] = []
    length, dropping, star_done, slash_pending = 0, False, False, False
    for p in sig.params:
        if slash_pending and p.kind != "positional_only":
            out.append("/")
            slash_pending = False
        if p.kind == "keyword_only" and not star_done:
            out.append("*")
            star_done = True
        text = _param_text(p, short)
        required = p.default is None and p.kind not in ("var_positional", "var_keyword")
        over = dropping or length + len(text) > PARAMS_WIDTH
        if short and required and over:
            text = _param_text(Param(p.name, p.kind), short)
        if short and not required and over:
            if not dropping:
                out.append("...")
                dropping = True
            star_done = star_done or p.kind == "var_positional"
            continue
        out.append(text)
        length += len(text) + 2
        star_done = star_done or p.kind == "var_positional"
        slash_pending = p.kind == "positional_only"
    if slash_pending:
        out.append("/")
    if out and out[-1] == "*":
        out.pop()
    returns = f" -> {annotation(sig.returns, short)}" if sig.returns else ""
    return f"({', '.join(out)}){returns}"


def lines(entry: Entry, indent: str = "") -> list[str]:
    """The entry's text lines: one per overload, then its members indented under it."""
    head = indent + ("async " if entry.is_async else "") + ("." if indent else "") + entry.name
    doc = f"  {entry.doc}" if entry.doc else ""
    match entry.kind:
        case "unresolved":
            out = [f"{head}  unresolved: {entry.reason}"]
        case "property":
            out = [f"{head}{f': {annotation(entry.value)}' if entry.value else ''}{doc}"]
        case "variable":
            out = [f"{head}{entry.value}{doc}"]
        case "module":
            out = [f"{head}  module {entry.value}{doc}"]
        case _:
            sigs = [signature_text(s) for s in entry.signatures] if entry.known else ["(...)"]
            out = [head + sig + (doc if i == 0 else "") for i, sig in enumerate(sigs or ["()"])]
    for member in entry.members:
        out += lines(member, indent + "  ")
    return out


def to_json(entry: Entry) -> dict[str, object]:
    data: dict[str, object] = {
        "name": entry.name,
        "kind": entry.kind,
        "module": entry.module,
        "resolved": entry.resolved,
    }
    if entry.is_async:
        data["async"] = True
    if entry.kind not in ("variable", "property", "module", "unresolved"):
        data["signatures"] = (
            [
                {
                    "text": signature_text(s, short=False),
                    "params": [
                        {
                            "name": p.name,
                            "kind": p.kind,
                            "annotation": p.annotation,
                            "default": p.default,
                        }
                        for p in s.params
                    ],
                    "returns": s.returns,
                }
                for s in entry.signatures
            ]
            if entry.known
            else None
        )
    if entry.value:
        data["value"] = entry.value
    if entry.doc:
        data["doc"] = entry.doc
    if entry.reason:
        data["reason"] = entry.reason
    if entry.members:
        data["members"] = [to_json(m) for m in entry.members]
    return data
