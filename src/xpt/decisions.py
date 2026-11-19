from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from xpt.accept import lock
from xpt.group import Group
from xpt.record import (
    DIRECTORY,
    SCHEMA_VERSION,
    json_int,
    json_object,
    json_str,
    read_jsonl,
    state_directory,
    versioned,
)
from xpt.site import CallSite, SiteKey
from xpt.value import canonical

FILE = "decisions.jsonl"

RejectStatus = Literal["rejected", "already_rejected", "skipped"]


@dataclass(frozen=True, slots=True)
class Rejection:
    """A call site's actual value that was rejected as a bug.

    `actual_sha` is the sha256 of the value's canonical JSON. A run that records another
    value at the site does not match, so the group is offered for acceptance again.
    """

    site: SiteKey
    actual_sha: str


@dataclass(frozen=True, slots=True)
class RejectOutcome:
    group: Group
    status: RejectStatus
    reason: str


def rejection(g: Group) -> Rejection | None:
    """The rejection that matches `g`, or None when it has no one actual value to reject."""
    if not g.acceptable or not isinstance(g.site, CallSite) or len(g.actuals) != 1:
        return None
    digest = hashlib.sha256(canonical(g.actuals[0]).encode("utf-8")).hexdigest()
    return Rejection(g.site.key, digest)


def load_rejections(root: Path) -> frozenset[Rejection]:
    path = root / DIRECTORY / FILE
    if not path.is_file():
        return frozenset()
    return frozenset(read_jsonl(root, path, _decode))


def mark(groups: Iterable[Group], rejections: frozenset[Rejection]) -> tuple[Group, ...]:
    return tuple(replace(g, rejected=rejection(g) in rejections) for g in groups)


def reject(root: Path, run: str, groups: Iterable[Group]) -> tuple[RejectOutcome, ...]:
    """Record a rejection of each group's actual value, once, in `.xpt/decisions.jsonl`.

    The file is rewritten whole and moved into place under `.xpt/accept.lock`, so a reader
    never sees a partial line and two writers cannot lose each other's records.
    """
    directory = state_directory(root)
    with lock(directory / "accept.lock"):
        known = set(load_rejections(root))
        outcomes, added = [], []
        for g in groups:
            r = rejection(g)
            if r is None:
                outcomes.append(RejectOutcome(g, "skipped", g.detail))
            elif r in known:
                outcomes.append(RejectOutcome(g, "already_rejected", ""))
            else:
                known.add(r)
                added.append(r)
                outcomes.append(RejectOutcome(g, "rejected", ""))
        if added:
            _append(directory / FILE, [_encode(r, run) for r in added])
    return tuple(outcomes)


def _append(path: Path, lines: list[str]) -> None:
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(before + "".join(f"{line}\n" for line in lines), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _encode(r: Rejection, run: str) -> str:
    path, qualname, callee, ordinal = r.site
    record = {
        "schema_version": SCHEMA_VERSION,
        "decision": "reject",
        "run": run,
        "site": {"path": path, "qualname": qualname, "callee": callee, "ordinal": ordinal},
        "actual_sha": r.actual_sha,
    }
    return json.dumps(record, ensure_ascii=False)


def _decode(data: object) -> Rejection:
    record = versioned(data)
    decision = record.get("decision")
    if decision != "reject":
        raise ValueError(f'decision must be "reject", got {decision!r}.')
    site = json_object(record.get("site"), "site")
    key = (
        json_str(site, "path"),
        json_str(site, "qualname"),
        json_str(site, "callee"),
        json_int(site, "ordinal"),
    )
    return Rejection(key, json_str(record, "actual_sha"))
