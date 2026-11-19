from __future__ import annotations

from xpt.path import Path, format_path


class UnsupportedValue(TypeError):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"{format_path(path)} {reason}; xpt cannot record it. "
            "Register an adapter or expect a field of it instead."
        )


def type_name(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"
