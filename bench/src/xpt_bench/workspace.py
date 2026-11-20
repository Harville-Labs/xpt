import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from xpt_bench.catalog import BENCH_ROOT, Task, Variant

REPO_ROOT = BENCH_ROOT.parent
BANNED_WORDS = (
    "bench",
    "eval",
    "harness",
    "judge",
    "experiment",
    "rubric",
    "score",
    "compare",
    "candidate",
    "arena",
    "oracle",
)
WHEEL_PLACEHOLDER = '"WHEEL"'
GIT_IDENTITY = ["-c", "user.name=dev", "-c", "user.email=dev@example.com"]
IGNORED = [".venv/", ".xpt/", "__pycache__/", ".pytest_cache/", "*.egg-info/"]
KEPT_ENV = {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"}


class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class Wheel:
    path: Path
    source: str
    commit: str | None
    sha256: str


def banned_words(extra: list[str]) -> list[str]:
    return [*BANNED_WORDS, *(w.lower() for w in extra if w)]


def neutrality_problems(text: str, words: list[str]) -> list[str]:
    lowered = text.lower()
    return sorted({w for w in words if w in lowered})


def clean_env(cache: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    # A parent Claude Code session exports CLAUDE* variables that would make the
    # child agent attach to that session instead of starting its own.
    env = {
        k: v
        for k, v in os.environ.items()
        if k in KEPT_ENV
        or not (k.startswith("CLAUDE") or k.startswith("XPT_") or k == "VIRTUAL_ENV")
    }
    env["UV_CACHE_DIR"] = str(cache)
    env.update(extra or {})
    return env


def codex_home(dest: Path, source: Path) -> Path:
    """A Codex home that holds only the login, so a run never sees the user's AGENTS.md or memories.

    `--ignore-user-config` skips `config.toml` but still loads `$CODEX_HOME/AGENTS.md`. The
    login is linked, not copied, so a token refresh during a run updates the user's own file.
    """
    auth = source / "auth.json"
    if not auth.is_file():
        raise WorkspaceError(
            f"{auth} not found. Run `codex login`, or set CODEX_HOME to its directory."
        )
    home = dest / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    link = home / "auth.json"
    if not link.is_symlink():
        link.symlink_to(auth)
    return home


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise WorkspaceError(f"`{' '.join(cmd)}` failed in {cwd}:\n{done.stderr or done.stdout}")
    return done.stdout


def _uv_build(src: Path, out_dir: Path) -> Path:
    before = set(out_dir.glob("*.whl"))
    _run(["uv", "build", "--wheel", "--out-dir", str(out_dir), str(src)], cwd=src)
    built = sorted(set(out_dir.glob("*.whl")) - before)
    if len(built) != 1:
        raise WorkspaceError(f"Expected one new wheel in {out_dir}, found {len(built)}.")
    return built[0]


def _commit_of(path: Path, ref: str = "HEAD") -> str | None:
    done = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else None


def build_wheel(source: str, out_dir: Path, repo: Path = REPO_ROOT) -> Wheel:
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = Path(source).expanduser()
    if candidate.suffix == ".whl" and candidate.is_file():
        path = Path(shutil.copy2(candidate, out_dir / candidate.name))
        commit = None
    elif candidate.is_dir():
        path = _uv_build(candidate.resolve(), out_dir)
        commit = _commit_of(candidate)
    else:
        commit = _commit_of(repo, source)
        if commit is None:
            raise WorkspaceError(
                f"--xpt {source!r} is not a wheel, a directory, or a git ref in {repo}."
            )
        archive = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", commit],
            capture_output=True,
            check=True,
        ).stdout
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(tmp, filter="data")
            path = _uv_build(Path(tmp), out_dir)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return Wheel(path=path, source=source, commit=commit, sha256=digest)


def neutral_dir(parent: Path, words: list[str], prefix: str = "tmp") -> Path:
    for _ in range(20):
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        if not neutrality_problems(path.name, words):
            return path
        path.rmdir()
    raise WorkspaceError(f"Could not create a neutral directory under {parent}.")


def sweep_root(base: Path | None, words: list[str]) -> Path:
    parent = Path(base or tempfile.gettempdir()).resolve()
    problems = neutrality_problems(str(parent), words)
    if problems:
        raise WorkspaceError(
            f"The work directory {parent} contains {', '.join(problems)}, which the agent "
            "would see. Pass --workroot with a neutral path."
        )
    return neutral_dir(parent, words)


def prepare(
    task: Task,
    variant: Variant,
    wheel: Path,
    root: Path,
    env: dict[str, str],
    words: list[str],
    overlay: Path | None = None,
) -> Path:
    project = neutral_dir(root, words) / task.workdir
    shutil.copytree(task.project(variant.fixture), project)
    if overlay is not None:
        shutil.copytree(overlay, project, dirs_exist_ok=True)
    pyproject = project / "pyproject.toml"
    relative = Path(os.path.relpath(wheel, project)).as_posix()
    pyproject.write_text(pyproject.read_text().replace(WHEEL_PLACEHOLDER, f'"{relative}"'))
    if variant.dev_deps:
        _run(["uv", "add", "--dev", *variant.dev_deps], cwd=project, env=env)
    _run(["uv", "sync"], cwd=project, env=env)
    _run(["git", "init", "-q"], cwd=project)
    (project / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (project / ".git" / "info" / "exclude").write_text("\n".join(IGNORED) + "\n")
    _run(["git", "add", "-A"], cwd=project)
    _run(["git", *GIT_IDENTITY, "commit", "-q", "--no-gpg-sign", "-m", "init"], cwd=project)
    return project


def diff(project: Path) -> str:
    _run(["git", "add", "-A"], cwd=project)
    return _run(["git", "diff", "--cached", "--no-color"], cwd=project)


def changed_files(project: Path) -> list[str]:
    return _run(["git", "diff", "--cached", "--name-only"], cwd=project).splitlines()


def read_tests(project: Path) -> dict[str, str]:
    tests = project / "tests"
    return {
        p.relative_to(project).as_posix(): p.read_text(errors="replace")
        for p in sorted(tests.rglob("*.py"))
        if "__pycache__" not in p.parts
    }
