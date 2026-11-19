"""Rebuild a protobuf message that sets extensions, for `expect.with_extensions`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def with_extensions(message: Any, extensions: Mapping[str, object]) -> Any:
    """Set each extension, named by its full name, on `message` and return the message."""
    pool = message.DESCRIPTOR.file.pool
    for name, value in extensions.items():
        field = pool.FindExtensionByName(name)
        if field.is_repeated:
            message.Extensions[field].extend(value)
        elif field.message_type is not None:
            message.Extensions[field].CopyFrom(value)
        else:
            message.Extensions[field] = value
    return message
