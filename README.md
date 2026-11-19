# xpt

Inline expect tests for Python, built so coding agents can work with them cheaply and safely.

```python
from xpt import expect

def test_parser():
    expect(parse("x + y"))
```

A run records what `expect` saw. `xpt failures` lists what changed, using paths into the value. `xpt accept <id>` writes one accepted value into the test source without running the tests again, and refuses if that source changed since the run.

Status: design. Nothing is implemented yet. Read [docs/design.md](docs/design.md) for the design and roadmap, and [docs/research.md](docs/research.md) for how expecttest, inline-snapshot, syrupy, and ApprovalTests compare. Plans are tracked as GitHub issues.

