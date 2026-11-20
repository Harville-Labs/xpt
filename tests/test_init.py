import re
from pathlib import Path

import pytest

from xpt.init import BEGIN, END, INSTRUCTIONS, Check, InitError, Probe, check, init

BLOCK = f"{BEGIN}\n{INSTRUCTIONS}{END}\n"
READY = Probe(pytest_version="9.0.1", plugin_installed=True, executable="/venv/bin/python")


def test_the_instructions_match_the_block_in_the_agents_guide():
    guide = Path(__file__).parent.parent / "docs" / "agents.md"
    fenced = re.search(r"```markdown\n(.*?)```", guide.read_text(encoding="utf-8"), re.DOTALL)
    assert fenced is not None
    assert fenced.group(1) == INSTRUCTIONS


def test_init_creates_agents_md_with_the_marked_block(tmp_path: Path):
    result = init(tmp_path, READY, {})
    assert result.status == "created"
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == BLOCK


def test_init_appends_the_block_after_a_blank_line_and_keeps_crlf_newlines(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_bytes(b"# Project\r\n\r\nRules.")
    assert init(tmp_path, READY, {}).status == "added"
    text = (tmp_path / "AGENTS.md").read_bytes().decode("utf-8")
    assert text == "# Project\r\n\r\nRules.\r\n\r\n" + BLOCK.replace("\n", "\r\n")


def test_running_init_twice_leaves_the_file_unchanged(tmp_path: Path):
    init(tmp_path, READY, {})
    before = (tmp_path / "AGENTS.md").read_bytes()
    assert init(tmp_path, READY, {}).status == "present"
    assert (tmp_path / "AGENTS.md").read_bytes() == before


def test_an_edited_block_is_kept_and_the_note_says_how_to_replace_it(tmp_path: Path):
    edited = f"Intro\n\n{BEGIN}\n## Tests with xpt\n\nOur own words.\n{END}\n"
    (tmp_path / "AGENTS.md").write_text(edited, encoding="utf-8")
    result = init(tmp_path, READY, {})
    assert result.status == "kept"
    assert result.note == (
        "The xpt block in AGENTS.md differs from this version of xpt and was left as it is. "
        "To replace it, delete it, markers included, and run xpt init again."
    )
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == edited


def test_a_hand_pasted_section_without_markers_is_not_duplicated(tmp_path: Path):
    pasted = f"# Project\n\n{INSTRUCTIONS}"
    (tmp_path / "AGENTS.md").write_text(pasted, encoding="utf-8")
    result = init(tmp_path, READY, {})
    assert result.status == "kept"
    assert "Tests with xpt" in result.note
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == pasted


@pytest.mark.parametrize("text", [f"{BEGIN}\nhalf\n", f"{END}\n{BEGIN}\n", f"{BLOCK}{BLOCK}"])
def test_unmatched_markers_are_an_error_and_nothing_is_written(tmp_path: Path, text: str):
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    with pytest.raises(InitError, match="markers"):
        init(tmp_path, READY, {})
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == text


def test_a_dry_run_reports_the_status_and_writes_nothing(tmp_path: Path):
    assert init(tmp_path, READY, {}, dry_run=True).status == "created"
    assert not (tmp_path / "AGENTS.md").exists()


def test_a_ready_environment_passes_both_checks(tmp_path: Path):
    assert check(tmp_path, READY, {}) == (
        Check("pytest", True, "pytest 9.0.1 in /venv/bin/python"),
        Check("plugin", True, "pytest loads xpt's plugin. No pytest configuration found."),
    )


def test_missing_or_old_pytest_is_a_problem(tmp_path: Path):
    missing = Probe(None, False, "/usr/bin/python3")
    [pytest_check, _] = check(tmp_path, missing, {})
    assert pytest_check == Check(
        "pytest",
        False,
        "pytest is not installed in /usr/bin/python3. Install it in the project's environment, "
        "for example uv add --dev pytest xpt.",
    )
    [old, _] = check(tmp_path, Probe("7.4.0", True, "/venv/bin/python"), {})
    assert old == Check(
        "pytest", False, "pytest 7.4.0 in /venv/bin/python is older than 8.0, which xpt needs."
    )


def test_an_uninstalled_plugin_is_a_problem(tmp_path: Path):
    [_, plugin] = check(tmp_path, Probe("9.0.1", False, "/venv/bin/python"), {})
    assert not plugin.ok
    assert plugin.detail.startswith("xpt's pytest plugin is not installed in /venv/bin/python.")


def test_addopts_that_disable_the_plugin_are_found_in_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-q -p no:xpt"\n', encoding="utf-8"
    )
    [_, plugin] = check(tmp_path, READY, {})
    assert plugin == Check(
        "plugin", False, "addopts in pyproject.toml passes -p no:xpt, which disables xpt's plugin."
    )


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("pytest.ini", "[pytest]\naddopts = -pno:xpt\n"),
        ("tox.ini", "[pytest]\naddopts = -p no:xpt\n"),
        ("setup.cfg", "[tool:pytest]\naddopts = -p no:xpt\n"),
        ("pyproject.toml", '[tool.pytest]\naddopts = ["-p", "no:xpt"]\n'),
    ],
)
def test_each_pytest_config_file_is_read(tmp_path: Path, name: str, text: str):
    (tmp_path / name).write_text(text, encoding="utf-8")
    [_, plugin] = check(tmp_path, READY, {})
    assert plugin.detail.startswith(f"addopts in {name} passes -p no:xpt")


def test_disabled_autoload_needs_the_plugin_named_explicitly(tmp_path: Path):
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    [_, plugin] = check(tmp_path, READY, env)
    assert plugin == Check(
        "plugin",
        False,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD is set, so pytest loads no installed plugins. "
        "Add -p xpt.pytest_plugin to addopts.",
    )
    env["PYTEST_ADDOPTS"] = "-p xpt.pytest_plugin"
    assert check(tmp_path, READY, env)[1].ok


def test_a_config_file_that_does_not_parse_is_a_problem(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest\n", encoding="utf-8")
    [_, plugin] = check(tmp_path, READY, {})
    assert not plugin.ok
    assert plugin.detail.startswith("pyproject.toml could not be read (")
