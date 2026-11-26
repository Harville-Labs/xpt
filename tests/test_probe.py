import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import probe_project
import pytest

from xpt import probe
from xpt.probe import Hit, Named, ProbeError, changed, load, parse_target
from xpt.value import Dict, Int, Seq, Str

MONITORING = sys.version_info >= (3, 12)
TRACERS = [
    pytest.param(
        "auto",
        "monitoring",
        id="monitoring",
        marks=pytest.mark.skipif(not MONITORING, reason="sys.monitoring is new in Python 3.12"),
    ),
    pytest.param(
        "auto",
        "settrace",
        id="settrace-default",
        marks=pytest.mark.skipif(
            MONITORING, reason="Python 3.12 and later use sys.monitoring by default"
        ),
    ),
    pytest.param("settrace", "settrace", id="settrace"),
]
TARGET = f"app/window.py:{probe_project.LINE}"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("XPT_TRACEBACK", raising=False)
    monkeypatch.delenv(probe.TRACER_ENV, raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    root = probe_project.write(tmp_path / "project")
    monkeypatch.setenv("PYTHONPATH", str(root))
    return root


def xpt_probe(root: Path, *argv: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "xpt", "probe", *argv],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **env},
        check=False,
    )


def probed(root: Path, *argv: str, **env: str) -> dict[str, Any]:
    """Probe with --json, the options before `--` and the Python command after it."""
    split = argv.index("--")
    options, command = argv[:split], argv[split + 1 :]
    ran = xpt_probe(root, *options, "--json", "--", sys.executable, *command, **env)
    data = json.loads(ran.stdout)
    assert data["exit_code"] == ran.returncode, ran.stderr
    return data


def values(record: dict[str, Any]) -> dict[str, Any]:
    return {
        v["name"]: v.get("value", v.get("unsupported", v.get("error"))) for v in record["values"]
    }


def test_the_probe_shows_the_off_by_one_in_a_failing_test(project: Path):
    ran = xpt_probe(project, TARGET, "--", sys.executable, "-m", "pytest", "-q", "tests")
    assert ran.returncode == 1
    report = ran.stderr.splitlines()
    assert report[:9] == [
        "app/window.py:6 hit 2 times",
        "  hit 1 in window_sums from tests/test_window.py:5 in test_window_sums:",
        "    values = [1, 2, 3, 4]",
        "    size = 2",
        "    sums = []",
        "    start = 0",
        "    chunk = [1, 2]",
        "  hit 2: sums[0]: added 3; start: 0 -> 1; chunk[0]: 1 -> 2; chunk[1]: 2 -> 3",
        report[8],
    ]
    assert report[8].startswith("Recorded in .xpt/probes/")
    assert report[8].endswith(".jsonl. Exit code 1.")
    assert "1 failed" in ran.stdout


@pytest.mark.parametrize(("tracer", "used"), TRACERS)
def test_each_tracer_records_every_hit_with_its_locals(project: Path, tracer: str, used: str):
    data = probed(project, TARGET, "--", "fail.py", XPT_PROBE_TRACER=tracer)
    assert data["tracers"] == [used]
    assert (data["exit_code"], data["hits"], data["processes"]) == (3, 2, 1)
    assert [values(r) for r in data["records"]] == [
        {"values": [1, 2, 3], "size": 1, "sums": [], "start": 0, "chunk": [1]},
        {"values": [1, 2, 3], "size": 1, "sums": [1], "start": 1, "chunk": [2]},
    ]
    assert [r["chain"] for r in data["records"]] == [["fail.py:3 in <module>"]] * 2


@pytest.mark.parametrize(("tracer", "used"), TRACERS)
def test_each_tracer_reaches_threads_and_module_level_lines(project: Path, tracer: str, used: str):
    threaded = probed(project, TARGET, "--", "threads.py", XPT_PROBE_TRACER=tracer)
    assert (threaded["hits"], threaded["tracers"]) == (2, [used])
    module = probed(project, "fail.py:3", "--", "fail.py", XPT_PROBE_TRACER=tracer)
    assert module["hits"] == 1
    assert module["records"][0]["function"] == "<module>"


def test_the_json_object_holds_the_whole_probe(project: Path):
    data = probed(project, "app/window.py:15", "--", "unsupported.py")
    [record] = data.pop("records")
    pid = record.pop("pid")
    assert isinstance(pid, int)
    assert data["file"].startswith(".xpt/probes/")
    del data["file"], data["tracers"]
    assert data == {
        "schema_version": 1,
        "command": "probe",
        "path": "app/window.py",
        "line": 15,
        "expr": None,
        "when": None,
        "max_hits": 100,
        "exit_code": 0,
        "hits": 1,
        "reached": 1,
        "processes": 1,
        "when_errors": 0,
        "errors": [],
    }
    assert record == {
        "hit": 1,
        "function": "describe",
        "chain": ["unsupported.py:3 in <module>"],
        "values": [
            {"name": "values", "value": [1, 2]},
            {"name": "sock", "unsupported": "<socket.socket>"},
        ],
    }


def test_json_keeps_the_command_output_off_stdout(project: Path):
    ran = xpt_probe(project, TARGET, "--json", "--", sys.executable, "-c", "print('hello')")
    assert json.loads(ran.stdout)["hits"] == 0
    assert ran.stderr == "hello\n"


def test_expr_records_only_the_expression(project: Path):
    data = probed(project, TARGET, "--expr", "values[start : start + size]", "--", "fail.py")
    assert [r["values"] for r in data["records"]] == [
        [{"name": "values[start : start + size]", "value": [1]}],
        [{"name": "values[start : start + size]", "value": [2]}],
    ]


def test_an_expr_that_raises_records_the_exception(project: Path):
    data = probed(project, TARGET, "--expr", "missing", "--max-hits", "1", "--", "fail.py")
    assert data["records"][0]["values"] == [
        {"name": "missing", "error": "NameError: name 'missing' is not defined"}
    ]


@pytest.mark.parametrize("form", ["option", "inline"])
def test_when_records_only_the_hits_where_it_is_true(project: Path, form: str):
    target = (TARGET, "--when", "start > 0") if form == "option" else (f"{TARGET} if start > 0",)
    data = probed(project, *target, "--", "fail.py")
    assert (data["when"], data["reached"], data["hits"]) == ("start > 0", 2, 1)
    assert [values(r)["start"] for r in data["records"]] == [1]


def test_a_when_that_is_never_true_says_how_often_the_line_was_reached(project: Path):
    ran = xpt_probe(project, TARGET, "--when", "start > 5", "--", sys.executable, "fail.py")
    assert ran.returncode == 3
    assert ran.stderr.splitlines()[0] == (
        "app/window.py:6 was reached 2 times in 1 Python process, but --when was never true."
    )


def test_a_when_that_raises_counts_as_false_and_is_reported(project: Path):
    ran = xpt_probe(project, TARGET, "--when", "nope > 1", "--", sys.executable, "fail.py")
    assert ran.stderr.splitlines()[:2] == [
        "app/window.py:6 was reached 2 times in 1 Python process, but --when was never true.",
        "  --when raised NameError: name 'nope' is not defined on 2 hits, counted as false",
    ]


def test_max_hits_stops_recording_but_keeps_counting(project: Path):
    data = probed(project, TARGET, "--max-hits", "1", "--", "fail.py")
    assert (data["hits"], len(data["records"])) == (2, 1)
    ran = xpt_probe(project, TARGET, "--max-hits", "1", "--", sys.executable, "fail.py")
    assert ran.stderr.splitlines()[0] == "app/window.py:6 hit 2 times, recorded 1"


def test_a_line_that_never_runs_is_reported(project: Path):
    ran = xpt_probe(project, "app/window.py:15", "--", sys.executable, "fail.py")
    assert ran.returncode == 3
    lines = ran.stderr.splitlines()
    assert lines[0] == "app/window.py:15 was never hit in 1 Python process."
    assert lines[1].startswith("Recorded in .xpt/probes/")


def test_a_command_without_python_loading_the_probe_is_reported(project: Path):
    ran = xpt_probe(project, TARGET, "--", sys.executable, "-I", "-c", "pass")
    assert ran.stderr.splitlines()[0].startswith(
        "app/window.py:6 was never hit, and no Python process loaded the probe."
    )


def test_child_processes_are_aggregated(project: Path):
    data = probed(project, TARGET, "--", "spawn.py")
    assert (data["hits"], data["processes"]) == (3, 2)
    assert [values(r)["values"] for r in data["records"]] == [[1, 2, 3], [1, 2, 3], [5, 6, 7]]
    assert len({r["pid"] for r in data["records"]}) == 2


def test_a_line_that_is_not_executable_is_refused_before_the_command_runs(project: Path):
    ran = xpt_probe(project, "app/window.py:2", "--", sys.executable, "-c", "print('ran')")
    assert (ran.returncode, ran.stdout) == (2, "")
    assert ran.stderr == (
        "xpt: error: app/window.py:2 is not an executable line: it is blank, a comment, or "
        "past the end of the file, so it can never be hit. The nearest executable lines are "
        "1 and 3.\n"
    )
    assert not (project / ".xpt").exists()


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        ((TARGET,), "xpt probe needs a command after --"),
        (("app/window.py", "--", "python"), "'app/window.py' is not FILE:LINE"),
        (("app/nope.py:1", "--", "python"), "there is no file app/nope.py."),
        ((TARGET, "--expr", "1 +", "--", "python"), "--expr '1 +' is not a Python expression"),
        ((TARGET, "--max-hits", "0", "--", "python"), "--max-hits must be 1 or more."),
        ((f"{TARGET} if a", "--when", "b", "--", "python"), "give the condition once"),
    ],
)
def test_usage_errors_exit_2(project: Path, argv: tuple[str, ...], error: str):
    ran = xpt_probe(project, *argv)
    assert ran.returncode == 2
    assert ran.stderr.startswith(f"xpt: error: {error}")


def test_the_command_exit_code_is_kept(project: Path):
    ran = xpt_probe(project, TARGET, "--", sys.executable, "-c", "raise SystemExit(7)")
    assert ran.returncode == 7
    assert xpt_probe(project, TARGET, "--", "no-such-command-xpt").returncode == 127


def test_probe_files_are_ignored_by_git_and_pruned(project: Path):
    for _ in range(probe.KEEP_PROBES + 1):
        xpt_probe(project, TARGET, "--", sys.executable, "-c", "pass")
    directory = project / ".xpt" / "probes"
    assert len(list(directory.glob("*.jsonl"))) == probe.KEEP_PROBES
    assert not list(directory.glob("*.d"))
    assert (project / ".xpt" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_load_rebuilds_values_as_data(tmp_path: Path):
    path = tmp_path / "probe.jsonl"
    header = {"kind": "probe", "path": "a.py", "line": 1, "max_hits": 1, "exit_code": 0}
    hit = {
        "kind": "hit",
        "pid": 1,
        "seq": 1,
        "function": "f",
        "chain": [],
        "values": [{"name": "x", "value": "__import__('os').system('false')"}],
    }
    path.write_text("\n".join(json.dumps(r) for r in (header, hit)) + "\n", encoding="utf-8")
    [loaded] = load(path).hits
    assert loaded.values == (Named("x", value=Str("__import__('os').system('false')")),)


def test_load_names_a_malformed_line(tmp_path: Path):
    path = tmp_path / "probe.jsonl"
    path.write_text('{"kind": "hit"}\n', encoding="utf-8")
    with pytest.raises(ProbeError, match=r"probe.jsonl:1 is not a probe record"):
        load(path)


def test_changed_lists_the_paths_that_differ():
    def hit(*named: Named) -> Hit:
        return Hit(1, 1, "f", (), named)

    before = hit(
        Named("i", value=Int(0)),
        Named("row", value=Dict(((Str("n"), Int(1)),))),
        Named("gone", value=Int(1)),
        Named("sock", unsupported="<socket.socket>"),
    )
    after = hit(
        Named("i", value=Int(1)),
        Named("row", value=Dict(((Str("n"), Int(1)), (Str("m"), Seq("list", ()))))),
        Named("sock", error="OSError: closed"),
        Named("new", value=Int(2)),
    )
    assert changed(before, after) == [
        "i: 0 -> 1",
        'row["m"]: added []',
        "sock: <socket.socket> -> <raised OSError: closed>",
        "new: added 2",
        "gone: removed",
    ]
    assert changed(before, before) == []


def test_parse_target_takes_an_inline_condition():
    assert parse_target("C:/src/a.py:42 if len(items) > 3") == probe.Target(
        Path("C:/src/a.py"), 42, "len(items) > 3"
    )
    with pytest.raises(ProbeError):
        parse_target("a.py:0")
