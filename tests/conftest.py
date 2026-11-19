from pathlib import Path

import pytest

from xpt import record

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> record.Recorder:
    fresh = record.Recorder(tmp_path, "20260101T000000-0000", "main", enabled=True, managed=True)
    monkeypatch.setattr(record, "recorder", fresh)
    return fresh
