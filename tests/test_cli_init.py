from pathlib import Path

import pytest
from cli_support import xpt

from xpt.init import BEGIN


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ("PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_PLUGINS"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_init_creates_agents_md_and_reports_both_checks(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "init")
    assert ran.code == 0
    lines = ran.out.splitlines()
    assert lines[0] == "Created AGENTS.md with the xpt block."
    assert lines[1].startswith("ok  pytest  pytest ")
    assert lines[2] == "ok  plugin  pytest loads xpt's plugin. No pytest configuration found."
    assert lines[3] == "Next: run pytest."
    assert (project / "AGENTS.md").read_text(encoding="utf-8").startswith(BEGIN)


def test_init_json_reports_the_status_and_checks_and_a_second_run_finds_the_block(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    xpt(capsys, "init")
    data = xpt(capsys, "init", "--json").json
    assert data["command"] == "init"
    assert data["status"] == "present"
    assert data["path"] == "AGENTS.md"
    assert [(c["name"], c["ok"]) for c in data["checks"]] == [("pytest", True), ("plugin", True)]


def test_a_dry_run_says_what_it_would_do_and_writes_nothing(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "init", "--dry-run")
    assert ran.out.splitlines()[0] == "Dry run, would create AGENTS.md with the xpt block."
    assert not (project / "AGENTS.md").exists()


def test_a_disabled_plugin_exits_1_and_says_how_to_fix_it(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    (project / "pytest.ini").write_text("[pytest]\naddopts = -p no:xpt\n", encoding="utf-8")
    ran = xpt(capsys, "init")
    assert ran.code == 1
    assert "problem  plugin  addopts in pytest.ini passes -p no:xpt" in ran.out
    assert ran.out.endswith("Fix the problems above and run xpt init again.\n")


def test_unmatched_markers_exit_2_with_the_error(project: Path, capsys: pytest.CaptureFixture[str]):
    (project / "AGENTS.md").write_text(f"{BEGIN}\n", encoding="utf-8")
    ran = xpt(capsys, "init")
    assert ran.code == 2
    assert ran.err.startswith("xpt: error: AGENTS.md has unmatched xpt markers.")


def test_a_dry_run_on_an_existing_file_says_it_would_add_the_block(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    (project / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    ran = xpt(capsys, "init", "--dry-run")
    assert ran.out.splitlines()[0] == "Dry run, would add the xpt block to AGENTS.md."
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "# Project\n"
