import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = BENCH_ROOT / "tasks"
VARIANTS_FILE = BENCH_ROOT / "variants.toml"
RUBRIC_FILE = BENCH_ROOT / "rubric.md"


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class WrongAccept:
    file: str
    pattern: re.Pattern[str]
    why: str


@dataclass(frozen=True)
class Task:
    name: str
    root: Path
    workdir: str
    prompt: str
    wrong_accepts: tuple[WrongAccept, ...]

    def project(self, fixture: str) -> Path:
        return self.root / fixture

    @property
    def check(self) -> Path:
        return self.root / "check"

    @property
    def solution(self) -> Path:
        return self.root / "solution"


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    env: dict[str, str] = field(default_factory=dict)
    fixture: str = "project"
    dev_deps: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentSpec:
    kind: str
    model: str | None

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.model or 'default'}"


AGENT_KINDS = ("claude", "codex")


def parse_agent(text: str) -> AgentSpec:
    kind, _, model = text.strip().partition(":")
    if kind not in AGENT_KINDS:
        raise CatalogError(f"Unknown agent {kind!r}. Use one of {', '.join(AGENT_KINDS)}.")
    return AgentSpec(kind, model if model and model != "default" else None)


def _require(data: dict, key: str, kind: type, where: Path):
    value = data.get(key)
    if not isinstance(value, kind):
        raise CatalogError(f"{where}: {key!r} must be a {kind.__name__}.")
    return value


def load_task(root: Path) -> Task:
    path = root / "task.toml"
    data = tomllib.loads(path.read_text())
    traps = []
    for entry in data.get("wrong_accept", []):
        traps.append(
            WrongAccept(
                file=_require(entry, "file", str, path),
                pattern=re.compile(_require(entry, "pattern", str, path)),
                why=_require(entry, "why", str, path),
            )
        )
    return Task(
        name=root.name,
        root=root,
        workdir=_require(data, "workdir", str, path),
        prompt=_require(data, "prompt", str, path),
        wrong_accepts=tuple(traps),
    )


def load_tasks(selection: str = "all", tasks_dir: Path = TASKS_DIR) -> list[Task]:
    available = {p.name: p for p in sorted(tasks_dir.iterdir()) if (p / "task.toml").is_file()}
    names = list(available) if selection == "all" else [n.strip() for n in selection.split(",")]
    missing = [n for n in names if n not in available]
    if missing:
        raise CatalogError(f"Unknown task {', '.join(missing)}. Tasks: {', '.join(available)}.")
    return [load_task(available[n]) for n in names]


def load_variants(selection: str = "all", path: Path = VARIANTS_FILE) -> list[Variant]:
    data = tomllib.loads(path.read_text())
    variants = {
        name: Variant(
            name=name,
            description=_require(table, "description", str, path),
            env={k: str(v) for k, v in table.get("env", {}).items()},
            fixture=table.get("fixture", "project"),
            dev_deps=tuple(table.get("dev_deps", ())),
        )
        for name, table in data.items()
    }
    names = list(variants) if selection == "all" else [n.strip() for n in selection.split(",")]
    missing = [n for n in names if n not in variants]
    if missing:
        raise CatalogError(
            f"Unknown variant {', '.join(missing)}. Variants: {', '.join(variants)}."
        )
    return [variants[n] for n in names]
