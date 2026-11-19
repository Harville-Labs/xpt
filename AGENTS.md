# AGENTS.md

xpt is an inline expect-testing library for Python. Read `docs/design.md` before changing behavior. It holds the data shapes, the accept lifecycle, and the roadmap. Plans are tracked as GitHub issues on `Harville-Labs/xpt`.

## Check

```bash
uv sync --locked && uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest
```

CI runs the same steps on Python 3.11 to 3.14 on Linux, macOS, and Windows.

## Layout

```
src/xpt/          the package
tests/            pytest tests
docs/design.md    design, decisions, roadmap
docs/research.md  prior-art notes with source citations
docs/prototypes/  throwaway prototypes, excluded from lint and type checks
```

## Invariants

- **No runtime dependencies.** `dependencies = []` in `pyproject.toml`, and `tests/test_package.py` enforces it. pytest support is an optional plugin. This repository therefore parses its own recorded files with the standard library instead of Pydantic. That departs from the shared Python standard on purpose: a test library that users install everywhere must not pull a dependency tree into their environment.
- **Recorded values are data.** Never `eval`, `exec`, or unpickle anything read from `.xpt/`. Values are rebuilt from the typed JSON tree only.
- **Source files are written only through accept.** Test runs never edit source. `pytest --expect-create` applies changes after the session through the same accept path.
- **Accept never writes stale changes.** It checks the expected argument's fingerprint and refuses if the source changed since the run.
- **Python 3.11 is the floor.** Call-site location depends on `code.co_positions()` and `co_qualname`.
