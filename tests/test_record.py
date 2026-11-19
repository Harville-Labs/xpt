import json
import re
from pathlib import Path

import pytest

from xpt.record import (
    MAX_RECORD_BYTES,
    Observation,
    Recorder,
    RecordError,
    load_run,
    new_run_id,
)
from xpt.site import CallSite, Unlocatable
from xpt.value import Dict, Float, Int, Null, Seq, Str

SITE = CallSite("tests/test_a.py", "test_a", 0, "ab" * 32, 7)


def observation(run: str, **changes: object) -> Observation:
    fields: dict[str, object] = {
        "run": run,
        "test": "tests/test_a.py::test_a",
        "site": SITE,
        "status": "mismatch",
        "expected": Seq("list", (Int(1),)),
        "actual": Seq("list", (Int(2),)),
        "detail": "",
    }
    fields.update(changes)
    return Observation(**fields)  # type: ignore[arg-type]


def finished(root: Path, run: str, *observations: Observation) -> Recorder:
    rec = Recorder(root, run, "main", enabled=True, managed=True)
    for o in observations:
        rec.record(o)
    rec.finish()
    return rec


def test_run_ids_are_a_sortable_utc_timestamp_and_a_random_suffix():
    first, second = new_run_id(), new_run_id()
    assert re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{4}", first)
    assert first[:15] <= second[:15]


def test_a_recorded_observation_round_trips_through_load_run(tmp_path: Path):
    values = Dict(((Str("x"), Float("nan")), (Int(1), Null())))
    recorded = [
        observation("20260101T000000-aaaa"),
        observation(
            "20260101T000000-aaaa",
            site=Unlocatable("no source", "<string>", 3),
            status="missing",
            expected=None,
            actual=values,
        ),
        observation("20260101T000000-aaaa", expected=Null(), actual=Str("ü\n")),
    ]
    finished(tmp_path, "20260101T000000-aaaa", *recorded)
    assert load_run(tmp_path) == recorded
    assert load_run(tmp_path, "20260101T000000-aaaa") == recorded


def test_each_worker_writes_its_own_file_and_load_run_reads_all_of_them(tmp_path: Path):
    run = "20260101T000000-aaaa"
    for worker in ("gw1", "gw0"):
        Recorder(tmp_path, run, worker, enabled=True, managed=True).record(
            observation(run, test=worker)
        )
    finished(tmp_path, run)
    files = sorted(p.name for p in (tmp_path / ".xpt" / "runs" / run).iterdir())
    assert files == ["gw0.jsonl", "gw1.jsonl"]
    assert [o.test for o in load_run(tmp_path)] == ["gw0", "gw1"]


def test_each_line_is_one_json_object_with_a_schema_version(tmp_path: Path):
    finished(tmp_path, "20260101T000000-aaaa", observation("20260101T000000-aaaa"))
    text = (tmp_path / ".xpt/runs/20260101T000000-aaaa/main.jsonl").read_text(encoding="utf-8")
    [line] = text.splitlines()
    assert json.loads(line)["schema_version"] == 1


def test_the_gitignore_is_written_on_first_use_and_never_rewritten(tmp_path: Path):
    rec = Recorder(tmp_path, "20260101T000000-aaaa", "main", enabled=True, managed=True)
    rec.record(observation(rec.run))
    gitignore = tmp_path / ".xpt" / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == "*\n"
    gitignore.write_text("*\n!keep\n", encoding="utf-8")
    finished(tmp_path, "20260101T000000-bbbb", observation("20260101T000000-bbbb"))
    assert gitignore.read_text(encoding="utf-8") == "*\n!keep\n"


def test_latest_is_written_only_when_the_session_finishes(tmp_path: Path):
    rec = Recorder(tmp_path, "20260101T000000-aaaa", "main", enabled=True, managed=True)
    rec.record(observation(rec.run))
    assert not (tmp_path / ".xpt" / "latest").exists()
    rec.finish()
    assert (tmp_path / ".xpt" / "latest").read_text(encoding="utf-8") == rec.run


def test_a_clean_run_after_a_failing_one_becomes_latest_with_nothing_recorded(tmp_path: Path):
    finished(tmp_path, "20260101T000000-aaaa", observation("20260101T000000-aaaa"))
    finished(tmp_path, "20260101T000001-bbbb")
    assert load_run(tmp_path) == []
    assert len(load_run(tmp_path, "20260101T000000-aaaa")) == 1


def test_a_session_with_nothing_recorded_creates_no_directory(tmp_path: Path):
    finished(tmp_path, "20260101T000000-aaaa")
    assert not (tmp_path / ".xpt").exists()


def test_finishing_keeps_only_the_five_newest_runs(tmp_path: Path):
    runs = [f"2026010{i}T000000-aaaa" for i in range(1, 8)]
    for run in reversed(runs):
        finished(tmp_path, run, observation(run))
    kept = sorted(p.name for p in (tmp_path / ".xpt" / "runs").iterdir())
    assert kept == runs[2:]


def test_a_disabled_recorder_writes_nothing(tmp_path: Path):
    finished(tmp_path, "20260101T000000-aaaa")
    rec = Recorder(tmp_path, "20260101T000000-aaaa", "main", enabled=False, managed=True)
    rec.record(observation(rec.run))
    rec.finish()
    assert not (tmp_path / ".xpt").exists()


def test_the_environment_switch_overrides_a_configured_recorder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("XPT_NO_RECORD", "1")
    rec = Recorder(tmp_path, "20260101T000000-aaaa", "main", enabled=True, managed=True)
    rec.configure(tmp_path, "20260101T000000-aaaa", "main", record=True)
    rec.record(observation(rec.run))
    rec.finish()
    assert not (tmp_path / ".xpt").exists()


def test_an_observation_over_the_size_cap_is_recorded_without_its_values(tmp_path: Path):
    big = Str("x" * MAX_RECORD_BYTES)
    finished(tmp_path, "20260101T000000-aaaa", observation("20260101T000000-aaaa", actual=big))
    [loaded] = load_run(tmp_path)
    assert (loaded.expected, loaded.actual, loaded.status) == (None, None, "mismatch")
    assert loaded.detail.startswith("too large: ")
    assert " bytes, sha256 " in loaded.detail
    path = tmp_path / ".xpt/runs/20260101T000000-aaaa/main.jsonl"
    assert path.stat().st_size < 1000


def malformed(tmp_path: Path, line: str) -> str:
    finished(tmp_path, "20260101T000000-aaaa", observation("20260101T000000-aaaa"))
    path = tmp_path / ".xpt/runs/20260101T000000-aaaa/main.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    with pytest.raises(RecordError) as info:
        load_run(tmp_path)
    return str(info.value)


def test_a_line_that_is_not_json_is_reported_with_its_file_and_line(tmp_path: Path):
    message = malformed(tmp_path, "{oops")
    assert message.startswith(".xpt/runs/20260101T000000-aaaa/main.jsonl:2: ")


def test_a_record_with_a_bad_field_names_the_field(tmp_path: Path):
    message = malformed(tmp_path / "a", json.dumps({"schema_version": 2}))
    assert message.endswith("main.jsonl:2: schema_version is 2, and this xpt reads version 1.")
    message = malformed(
        tmp_path / "b",
        json.dumps(
            {
                "schema_version": 1,
                "run": "r",
                "test": "t",
                "site": {"kind": "unlocatable", "reason": "r", "path": "p", "line": 1},
                "status": "mismatch",
                "expected": {"set": "no"},
                "actual": 1,
                "detail": "",
            }
        ),
    )
    assert "main.jsonl:2: expected: unknown tagged value" in message


def test_a_run_id_that_could_escape_the_runs_directory_is_refused(tmp_path: Path):
    finished(tmp_path, "20260101T000000-aaaa", observation("20260101T000000-aaaa"))
    (tmp_path / ".xpt" / "latest").write_text("../../etc", encoding="utf-8")
    with pytest.raises(RecordError, match="not a run id"):
        load_run(tmp_path)
    with pytest.raises(RecordError, match="not a run id"):
        load_run(tmp_path, "..")


def test_loading_before_any_run_says_to_run_the_tests(tmp_path: Path):
    with pytest.raises(RecordError, match="Run the tests"):
        load_run(tmp_path)
    with pytest.raises(RecordError, match="no run 20260101T000000-aaaa"):
        load_run(tmp_path, "20260101T000000-aaaa")
