import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from xpt import expect
from xpt.record import Recorder, load_run
from xpt.serialize import UnsupportedValue
from xpt.site import CallSite
from xpt.value import Int, Seq, Str

HERE = Path(__file__).resolve()


def fails(*args: object, **kwargs: Any) -> str:
    with pytest.raises(AssertionError) as info:
        expect(*args, **kwargs)  # type: ignore[arg-type]
    return str(info.value)


def recorded(rec: Recorder):
    rec.finish()
    return load_run(rec.root)


def test_a_passing_expect_writes_nothing(recorder: Recorder):
    expect([1], [1])
    expect(object, object)
    recorder.finish()
    assert not (recorder.root / ".xpt").exists()


def test_a_missing_expectation_records_the_actual_value_and_its_call_site(recorder: Recorder):
    fails({"a": 1})
    [o] = recorded(recorder)
    assert (o.run, o.status, o.expected, o.detail) == (recorder.run, "missing", None, "")
    assert o.actual is not None
    assert o.test == "fails"
    assert isinstance(o.site, CallSite)
    assert o.site.qualname == "fails"


def test_a_mismatch_records_both_values_at_the_test_call_site(recorder: Recorder):
    with pytest.raises(AssertionError):
        expect(["b"], [1])
    [o] = recorded(recorder)
    assert (o.status, o.expected, o.actual) == (
        "mismatch",
        Seq("list", (Int(1),)),
        Seq("list", (Str("b"),)),
    )
    site = o.site
    assert isinstance(site, CallSite)
    assert site.path == HERE.as_posix()
    assert site.qualname == "test_a_mismatch_records_both_values_at_the_test_call_site"
    assert site.arg_sha != ""


def check_one(value: object) -> None:
    __tracebackhide__ = True
    expect(value, 1, _depth=1)


def test_depth_one_records_the_call_site_of_the_helper_caller(recorder: Recorder):
    with pytest.raises(AssertionError):
        check_one(2)
    [o] = recorded(recorder)
    assert isinstance(o.site, CallSite)
    assert o.site.qualname == "test_depth_one_records_the_call_site_of_the_helper_caller"
    assert o.test == "test_depth_one_records_the_call_site_of_the_helper_caller"


def test_an_unsupported_value_is_recorded_with_the_reason_and_still_fails(recorder: Recorder):
    with pytest.raises(UnsupportedValue) as info:
        expect({"f": len}, {"f": 1})
    [o] = recorded(recorder)
    assert (o.status, o.expected, o.actual) == ("unsupported", None, None)
    assert o.detail == str(info.value)
    assert "builtin_function_or_method" in o.detail


def test_an_unwritable_record_directory_still_raises_the_assertion_with_a_note(
    recorder: Recorder,
):
    (recorder.root / ".xpt").write_text("in the way", encoding="utf-8")
    lines = fails([1], [2]).splitlines()
    assert lines[1] == "  value[0]: 2 -> 1"
    assert lines[-1].startswith("xpt could not record this failure: ")


def test_no_record_from_the_environment_writes_nothing(tmp_path: Path):
    run = run_unittest(tmp_path, {"XPT_NO_RECORD": "1"})
    assert run.returncode == 1
    assert not (tmp_path / ".xpt").exists()


def test_plain_unittest_records_with_the_qualname_and_marks_the_run_latest(tmp_path: Path):
    run = run_unittest(tmp_path, {})
    assert run.returncode == 1, run.stderr
    [o] = load_run(tmp_path)
    assert o.test == "T.test_it"
    assert isinstance(o.site, CallSite)
    assert (o.site.path, o.site.qualname, o.site.line) == ("test_plain.py", "T.test_it", 6)


def run_unittest(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_plain.py").write_text(
        textwrap.dedent("""\
            import unittest
            from xpt import expect

            class T(unittest.TestCase):
                def test_it(self):
                    expect([1], [2])
            """),
        encoding="utf-8",
    )
    clean = {
        k: v for k, v in os.environ.items() if k not in ("XPT_NO_RECORD", "PYTEST_XDIST_WORKER")
    }
    return subprocess.run(
        [sys.executable, "-m", "unittest", "test_plain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=clean | env,
    )
