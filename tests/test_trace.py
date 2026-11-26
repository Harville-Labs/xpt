import importlib.util
import json
import sys
from pathlib import Path

import pytest

from xpt import sources, trace
from xpt.record import RecordError

MODULE = """\
RATE = 0.1


def total(price):
    discounted = price * (1 - RATE)
    return round(discounted, 2)


def unused():
    return 0
"""


@pytest.fixture
def tracer(tmp_path: Path):
    if sys.gettrace() is not None:
        pytest.skip("Python 3.11 allows one tracer, and this run already has one.")
    (tmp_path / "shop.py").write_text(MODULE, encoding="utf-8")
    found = trace.Tracer(tmp_path)
    found.start()
    yield found
    found.stop()


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"shop_{path.parent.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_context_holds_the_project_lines_it_ran_and_definitions_hold_the_import(
    tracer: trace.Tracer, tmp_path: Path
):
    shop = load(tmp_path / "shop.py")
    tracer.begin()
    shop.total(100)
    assert tracer.end() == {"shop.py": frozenset({5, 6})}
    assert tracer.definitions() == {"shop.py": frozenset({1, 4, 9})}


def test_each_context_records_a_line_again(tracer: trace.Tracer, tmp_path: Path):
    shop = load(tmp_path / "shop.py")
    for _ in range(2):
        tracer.begin()
        shop.total(1)
        assert tracer.end() == {"shop.py": frozenset({5, 6})}
    tracer.begin()
    assert tracer.end() == {}


def test_a_session_tracer_keeps_every_line_across_contexts(tmp_path: Path):
    if sys.gettrace() is not None:
        pytest.skip("Python 3.11 allows one tracer, and this run already has one.")
    (tmp_path / "shop.py").write_text(MODULE, encoding="utf-8")
    tracer = trace.Tracer(tmp_path, session=True)
    tracer.start()
    try:
        shop = load(tmp_path / "shop.py")
        tracer.begin()
        shop.total(1)
        tracer.end()
        shop.unused()
    finally:
        tracer.stop()
    assert tracer.everything() == {"shop.py": frozenset({1, 4, 5, 6, 9, 10})}
    assert tracer.imported() == frozenset()


def test_code_outside_the_root_is_not_recorded(tmp_path: Path):
    if sys.gettrace() is not None:
        pytest.skip("Python 3.11 allows one tracer, and this run already has one.")
    (tmp_path / "shop.py").write_text(MODULE, encoding="utf-8")
    inside = tmp_path / "project"
    inside.mkdir()
    tracer = trace.Tracer(inside)
    tracer.start()
    try:
        shop = load(tmp_path / "shop.py")
        tracer.begin()
        shop.total(1)
        assert tracer.end() == {}
    finally:
        tracer.stop()


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("src/app/load.py", "src/app/load.py"),
        (".venv/lib/thing.py", None),
        ("lib/site-packages/thing.py", None),
        ("env/lib/thing.py", None),
        ("src/app/data.txt", None),
    ],
)
def test_project_file_skips_venvs_hidden_dirs_and_installed_packages(
    tmp_path: Path, relative: str, expected: str | None
):
    (tmp_path / "env").mkdir()
    (tmp_path / "env" / "pyvenv.cfg").write_text("", encoding="utf-8")
    assert sources.project_file(tmp_path, str(tmp_path / relative)) == expected


def test_project_file_refuses_paths_outside_the_root_and_non_files(tmp_path: Path):
    assert sources.project_file(tmp_path / "a", str(tmp_path / "b.py")) is None
    assert sources.project_file(tmp_path, "<frozen os>") is None


RUN = "20260101T000000-aaaa"


def test_write_and_load_join_every_worker_and_add_each_files_definitions(tmp_path: Path):
    (tmp_path / "shop.py").write_text(MODULE, encoding="utf-8")
    definitions = {"shop.py": frozenset({1, 4})}
    trace.write(tmp_path, RUN, "gw0", {"t::a": {"shop.py": frozenset({5})}}, definitions)
    trace.write(tmp_path, RUN, "gw1", {"t::b": {"shop.py": frozenset({6})}}, {})
    found = trace.load(tmp_path, RUN)
    assert found is not None
    assert found.tests == {"t::a": {"shop.py": {1, 4, 5}}, "t::b": {"shop.py": {6}}}
    assert found.files == {"shop.py": trace.file_sha(tmp_path / "shop.py")}
    assert found.ran(["t::a", "t::b"]) == {"shop.py": {1, 4, 5, 6}}
    assert found.ran(["t::c"]) is None


def test_a_run_without_a_trace_loads_as_none(tmp_path: Path):
    assert trace.load(tmp_path, RUN) is None


@pytest.mark.parametrize(
    "data",
    [
        {"schema_version": 2, "files": {}, "tests": {}},
        {"schema_version": 1, "files": {"a.py": 1}, "tests": {}},
        {"schema_version": 1, "files": {}, "tests": {"t": {"a.py": ["1"]}}},
        {"schema_version": 1, "files": {}, "tests": {"t": {"a.py": [True]}}},
        {"schema_version": 1, "files": {}, "tests": []},
    ],
)
def test_a_malformed_trace_names_its_file(tmp_path: Path, data: object):
    run_dir = tmp_path / ".xpt" / "runs" / RUN
    run_dir.mkdir(parents=True)
    (run_dir / "main.trace.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RecordError, match=rf"\.xpt/runs/{RUN}/main\.trace\.json: "):
        trace.load(tmp_path, RUN)
