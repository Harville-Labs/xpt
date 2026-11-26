import sys
from pathlib import Path

import pytest
from cli_support import xpt

from xpt.lockfile import same_version


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(sys, "path", [str(root), *sys.path[1:]])
    return root


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "site-packages"
    directory.mkdir()
    monkeypatch.setattr(sys, "path", [*sys.path, str(directory)])
    return directory


def distribution(site: Path, name: str, version: str) -> None:
    info = site / f"{name}-{version}.dist-info"
    info.mkdir()
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    (info / "METADATA").write_text(metadata, encoding="utf-8")
    (info / "top_level.txt").write_text(f"{name}\n", encoding="utf-8")
    (info / "RECORD").write_text(f"{name}/__init__.py,,\n", encoding="utf-8")
    (site / name).mkdir()
    (site / name / "__init__.py").write_text("", encoding="utf-8")


def problems(out: str) -> list[str]:
    return out.splitlines()[1:]


def test_a_clean_environment_prints_one_line_and_exits_0(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "env")
    assert ran.code == 0
    [line] = ran.out.splitlines()
    info = sys.version_info
    assert line.startswith(f"python {info.major}.{info.minor}.{info.micro} at ")
    assert line.endswith(", no lockfile)")


def test_a_local_module_named_like_the_standard_library_is_shadowing(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    (project / "json.py").write_text("", encoding="utf-8")
    (project / "logging").mkdir()
    (project / "logging" / "__init__.py").write_text("", encoding="utf-8")
    (project / "notes").mkdir()
    (project / "sys.py").write_text("", encoding="utf-8")
    ran = xpt(capsys, "env")
    assert ran.code == 1
    assert problems(ran.out) == [
        "shadowing: json.py shadows the standard library module json",
        "shadowing: logging shadows the standard library module logging",
    ]


def test_a_local_module_named_like_an_installed_package_is_shadowing_but_the_project_is_not(
    project: Path, site: Path, capsys: pytest.CaptureFixture[str]
):
    distribution(site, "fakepkg", "1.0")
    distribution(site, "demo", "0.1")
    (project / "fakepkg.py").write_text("", encoding="utf-8")
    (project / "demo").mkdir()
    (project / "demo" / "__init__.py").write_text("", encoding="utf-8")
    ran = xpt(capsys, "env")
    assert problems(ran.out) == [
        "shadowing: fakepkg.py shadows fakepkg from the installed distribution fakepkg"
    ]


def test_sys_path_0_is_searched_too_and_shown_absolute(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "typing.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(scripts), *sys.path])
    ran = xpt(capsys, "env")
    assert problems(ran.out) == [
        f"shadowing: {scripts / 'typing.py'} shadows the standard library module typing"
    ]


UV_LOCK = """\
version = 1
requires-python = ">=3.11"

[[package]]
name = "demo"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "fakepkg" },
    { name = "Ghost_Pkg", extra = ["fast"] },
    { name = "winonly", marker = "sys_platform == 'win32'" },
]

[package.dev-dependencies]
dev = [{ name = "devtool" }]

[[package]]
name = "fakepkg"
version = "2.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "ghost-pkg"
version = "1.0"
source = { registry = "https://pypi.org/simple" }

[package.optional-dependencies]
fast = [{ name = "speedup" }]

[[package]]
name = "speedup"
version = "3.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "same"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "winonly"
version = "1.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "devtool"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
"""


def test_uv_lock_drift_reports_mismatched_and_required_missing_packages(
    project: Path, site: Path, capsys: pytest.CaptureFixture[str]
):
    distribution(site, "fakepkg", "1.0")
    distribution(site, "same", "1.0")
    (project / "uv.lock").write_text(UV_LOCK, encoding="utf-8")
    ran = xpt(capsys, "env")
    assert ran.code == 1
    assert ran.out.splitlines()[0].endswith(", differs from uv.lock)")
    assert problems(ran.out) == [
        "mismatch: fakepkg 1.0 installed, uv.lock wants 2.0",
        "missing: ghost-pkg is not installed, uv.lock wants 1.0",
        "missing: speedup is not installed, uv.lock wants 3.1",
    ]


def test_a_matching_uv_lock_says_so_on_the_interpreter_line(
    project: Path, site: Path, capsys: pytest.CaptureFixture[str]
):
    distribution(site, "fakepkg", "2.0")
    lock = '[[package]]\nname = "fakepkg"\nversion = "2.0"\n'
    (project / "uv.lock").write_text(lock, encoding="utf-8")
    ran = xpt(capsys, "env")
    assert ran.code == 0
    [line] = ran.out.splitlines()
    assert line.endswith(", matches uv.lock)")


POETRY_LOCK = """\
[[package]]
name = "fakepkg"
version = "1.0"
optional = false
groups = ["main"]

[[package]]
name = "ghost"
version = "2.0"
optional = false
groups = ["main"]

[[package]]
name = "devonly"
version = "1.0"
optional = false
groups = ["dev"]

[[package]]
name = "maconly"
version = "1.0"
optional = false
groups = ["main"]
markers = "sys_platform == 'darwin'"

[[package]]
name = "old-style"
version = "4.0"
optional = false
category = "main"
"""


def test_poetry_lock_reports_main_packages_that_are_missing(
    project: Path, site: Path, capsys: pytest.CaptureFixture[str]
):
    distribution(site, "fakepkg", "1.0")
    (project / "poetry.lock").write_text(POETRY_LOCK, encoding="utf-8")
    ran = xpt(capsys, "env")
    assert problems(ran.out) == [
        "missing: ghost is not installed, poetry.lock wants 2.0",
        "missing: old-style is not installed, poetry.lock wants 4.0",
    ]


def test_requirements_files_compare_pinned_lines(
    project: Path, site: Path, capsys: pytest.CaptureFixture[str]
):
    distribution(site, "fakepkg", "1.0")
    (project / "requirements.txt").write_text(
        "# pinned\n"
        "FakePkg[extra]==1.1 \\\n"
        "    --hash=sha256:abc\n"
        "ghost==1.0 ; sys_platform == 'win32'\n"
        "-r other.txt\n"
        "loose>=1\n"
        "wild==1.*\n",
        encoding="utf-8",
    )
    (project / "requirements-dev.txt").write_text("devtool==0.3  # tests\n", encoding="utf-8")
    ran = xpt(capsys, "env")
    assert ran.out.splitlines()[0].endswith(
        ", differs from requirements-dev.txt and requirements.txt)"
    )
    assert problems(ran.out) == [
        "missing: devtool is not installed, requirements-dev.txt wants 0.3",
        "mismatch: FakePkg 1.0 installed, requirements.txt wants 1.1",
    ]


def test_an_unreadable_lockfile_is_a_problem(project: Path, capsys: pytest.CaptureFixture[str]):
    (project / "uv.lock").write_text("[[package]\n", encoding="utf-8")
    ran = xpt(capsys, "env")
    assert ran.code == 1
    [line] = problems(ran.out)
    assert line.startswith("lockfile: uv.lock could not be read: ")


def test_interpreter_problems(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("VIRTUAL_ENV", str(project / "other-venv"))
    (project / ".python-version").write_text("2.7\n", encoding="utf-8")
    ran = xpt(capsys, "env")
    assert ran.code == 1
    info = sys.version_info
    assert problems(ran.out) == [
        f"interpreter: VIRTUAL_ENV is other-venv, but xpt runs in {Path(sys.prefix)}.",
        f"interpreter: .python-version asks for 2.7, but this is python "
        f"{info.major}.{info.minor}.{info.micro}.",
    ]


def test_the_root_is_found_from_a_subdirectory(project: Path, capsys: pytest.CaptureFixture[str]):
    (project / "json.py").write_text("", encoding="utf-8")
    (project / "src").mkdir()
    ran = xpt(capsys, "env", "--root", "src")
    assert problems(ran.out) == ["shadowing: json.py shadows the standard library module json"]


def test_env_json_carries_every_section(
    project: Path, site: Path, capsys: pytest.CaptureFixture[str]
):
    distribution(site, "fakepkg", "1.0")
    (project / "json.py").write_text("", encoding="utf-8")
    (project / "fakepkg.py").write_text("", encoding="utf-8")
    (project / "uv.lock").write_text(UV_LOCK, encoding="utf-8")
    data = xpt(capsys, "env", "--json").json
    interpreter = data.pop("interpreter")
    assert set(interpreter) == {
        "version",
        "executable",
        "venv",
        "lockfiles",
        "matches_lock",
        "problems",
    }
    assert interpreter["lockfiles"] == ["uv.lock"]
    assert interpreter["matches_lock"] is False
    assert interpreter["problems"] == []
    assert data == {
        "schema_version": 1,
        "command": "env",
        "shadowing": [
            {
                "path": "fakepkg.py",
                "module": "fakepkg",
                "stdlib": False,
                "distributions": ["fakepkg"],
            },
            {"path": "json.py", "module": "json", "stdlib": True, "distributions": []},
        ],
        "drift": [
            {"name": "fakepkg", "lockfile": "uv.lock", "locked": ["2.0"], "installed": "1.0"},
            {"name": "ghost-pkg", "lockfile": "uv.lock", "locked": ["1.0"], "installed": None},
            {"name": "speedup", "lockfile": "uv.lock", "locked": ["3.1"], "installed": None},
        ],
        "unreadable": [],
    }


def test_env_name_locates_an_installed_package(
    project: Path, site: Path, capsys: pytest.CaptureFixture[str]
):
    distribution(site, "fakepkg", "1.0")
    ran = xpt(capsys, "env", "fakepkg")
    assert ran.code == 0
    assert ran.out == f"fakepkg 1.0 from {site / 'fakepkg' / '__init__.py'}\n"


def test_env_name_reports_a_local_module_that_shadows_the_standard_library(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    (project / "json.py").write_text("", encoding="utf-8")
    ran = xpt(capsys, "env", "json")
    assert ran.code == 1
    assert ran.out.splitlines() == [
        "json from json.py",
        "shadowing: json.py shadows the standard library module json",
    ]


def test_env_name_finds_a_submodule_without_importing_its_package(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    (project / "boom").mkdir()
    (project / "boom" / "__init__.py").write_text("raise SystemExit('imported')\n")
    (project / "boom" / "sub.py").write_text("raise SystemExit('imported')\n")
    ran = xpt(capsys, "env", "boom.sub")
    assert ran.code == 0
    assert ran.out == "boom.sub from boom/sub.py\n"
    assert "boom" not in sys.modules


def test_env_name_for_the_standard_library_built_ins_and_missing_modules(
    project: Path, capsys: pytest.CaptureFixture[str]
):
    stdlib = xpt(capsys, "env", "json").out
    assert stdlib.startswith("json from ")
    assert stdlib.endswith("__init__.py (standard library)\n")
    assert xpt(capsys, "env", "sys").out == "sys is built into the interpreter\n"
    missing = xpt(capsys, "env", "no_such_module_here")
    assert missing.code == 1
    assert missing.out == (
        "no_such_module_here is not importable here, with the project root first on sys.path\n"
    )


def test_env_name_json(project: Path, site: Path, capsys: pytest.CaptureFixture[str]):
    distribution(site, "fakepkg", "1.0")
    data = xpt(capsys, "env", "fakepkg", "--json").json
    assert set(data["interpreter"]) == {"version", "executable", "venv", "problems"}
    assert data["module"] == {
        "name": "fakepkg",
        "kind": "file",
        "origin": str(site / "fakepkg" / "__init__.py"),
        "locations": [],
        "stdlib": False,
        "distribution": "fakepkg",
        "version": "1.0",
        "shadows": None,
    }


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [("1.0", "1.0.0", True), ("2.0", "2.0.1", False), ("1.0rc1", "1.0RC1", True)],
)
def test_same_version(a: str, b: str, same: bool):
    assert same_version(a, b) is same
