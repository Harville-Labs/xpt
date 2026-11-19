"""A short stand-in for an array or tensor too large to write inline."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True, kw_only=True)
class Digest:
    """What `expect.digest(...)` builds. It equals an array with the same bytes.

    `kind`, `shape`, `dtype`, `device`, and `sha256` decide equality. `min`, `max`, and `mean`
    are there for the reader and are never compared.
    """

    kind: str
    shape: tuple[int, ...]
    dtype: Any
    device: str = ""
    sha256: str
    min: float | None = field(default=None, compare=False)
    max: float | None = field(default=None, compare=False)
    mean: float | None = field(default=None, compare=False)


def of(obj: object) -> Digest | None:
    """The digest of a NumPy array or PyTorch tensor, or None for any other value."""
    names = {f"{c.__module__}.{c.__qualname__}" for c in type(obj).__mro__}
    if "numpy.ndarray" in names:
        return of_numpy(obj)
    if "torch.Tensor" in names:
        return of_torch(obj)
    return None


def of_numpy(a: Any) -> Digest:
    dtype = a.dtype.newbyteorder("<") if a.dtype.byteorder == ">" else a.dtype
    name = dtype.str if dtype.kind == "U" else dtype.name
    stats = a.dtype.kind in "iuf" and a.size
    return Digest(
        kind="numpy",
        shape=tuple(a.shape),
        dtype=name,
        sha256=_sha256(a.astype(dtype, copy=False).tobytes()),
        min=a.min().item() if stats else None,
        max=a.max().item() if stats else None,
        mean=_round(a.mean().item()) if stats else None,
    )


def of_torch(t: Any) -> Digest:
    torch = sys.modules["torch"]
    flat = t.detach().cpu().contiguous().reshape(-1)
    stats = flat.numel() and not (flat.is_complex() or flat.dtype == torch.bool)
    return Digest(
        kind="torch",
        shape=tuple(t.shape),
        dtype=t.dtype,
        device="" if t.device.type == "cpu" else str(t.device),
        sha256=_sha256(flat.view(torch.uint8).numpy().tobytes()),
        min=flat.min().item() if stats else None,
        max=flat.max().item() if stats else None,
        mean=_round(flat.double().mean().item()) if stats else None,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _round(x: float) -> float:
    return float(f"{x:.6g}")
