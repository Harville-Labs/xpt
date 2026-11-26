# Probing a line

When a test or a script misbehaves, the usual loop is to add `print` calls, rerun, read the output, and delete the prints. Each round edits the source and costs a full run, and a debugger needs someone at the prompt. `xpt probe` records the values at one line while a command runs, without editing the source and without an interactive session.

```bash
xpt probe app/window.py:6 -- pytest tests/test_window.py
```

```
app/window.py:6 hit 2 times
  hit 1 in window_sums from tests/test_window.py:5 in test_window_sums:
    values = [1, 2, 3, 4]
    size = 2
    sums = []
    start = 0
    chunk = [1, 2]
  hit 2: sums[0]: added 3; start: 0 -> 1; chunk[0]: 1 -> 2; chunk[1]: 2 -> 3
Recorded in .xpt/probes/20261126T074616-6753.jsonl. Exit code 1.
```

Four values in windows of 2 make 3 windows, but the line ran twice and `start` stopped at 1. That points at the loop's bound, `range(len(values) - size)`, which should be `range(len(values) - size + 1)`.

## What it prints

- `path:line hit N times`, with the number of processes when there was more than one, and how many hits were recorded when `--max-hits` stopped recording.
- The first hit: its function, the project frames that called it (nearest first, as in the [compact traceback](traceback.md)), and its local variables as one-line value trees. A value xpt cannot record prints as its type, such as `<socket.socket>`, never as a `repr`.
- Each later hit on one line, with only the paths that changed since the hit before it, as `path: old -> new`, `path: added value`, or `path: removed`. `same` means nothing changed. When a hit came from another call chain or process, its `from` chain is printed too. Ten later hits are shown; the file holds the rest.
- Where the hits were recorded, and the command's exit code.

The values are read when the line is about to run, as at a breakpoint, so the line's own assignments are not in them yet.

The summary goes to stderr after the command finishes, so the command's own stdout is unchanged. `xpt probe` exits with the command's exit code.

## Options

```text
xpt probe FILE:LINE [--expr EXPR] [--when COND] [--max-hits N] [--json] -- command [arg ...]
```

| Option | Meaning |
|---|---|
| `--expr EXPR` | Record this expression, evaluated in the frame, instead of the locals. If it raises, the hit records the exception. |
| `--when COND` | Record only the hits where this condition, evaluated in the frame, is true. A condition that raises counts as false, and the summary says how often it raised. `FILE:LINE if COND` is the same. |
| `--max-hits N` | Record the first `N` hits in each process. Default 100. Later hits are still counted. |
| `--json` | Print one JSON object on stdout instead of the summary. The command's stdout goes to stderr, so stdout holds only the JSON. |

`FILE` is relative to the current directory. `EXPR` and `COND` are Python you type on the command line, run in your own program. Nothing read back from `.xpt/` is ever evaluated.

```bash
xpt probe 'src/app/parse.py:42 if len(items) > 3' -- python -m app
xpt probe src/app/parse.py:42 --expr 'items[-1]' --max-hits 5 -- pytest -x
```

## When it finds nothing

- A blank line, a comment, or a line past the end of the file can never run, so `xpt probe` refuses it before running the command, names the nearest lines that can, and exits 2.
- A line that never ran prints `app/window.py:15 was never hit in 1 Python process.`
- With `--when`, it says how often the line was reached: `app/window.py:6 was reached 2 times in 1 Python process, but --when was never true.`
- When no Python process loaded the probe, it says so. `python -I` and `python -E` ignore `PYTHONPATH`, which is how the probe gets into the command's processes.

## Subprocesses and threads

Every Python process the command starts records its own hits, including pytest-xdist workers and `subprocess` children, as long as it starts from a Python that runs `sitecustomize` and can import xpt. The hits are merged in time order. Threads are probed too.

## The JSON object

```json
{
  "schema_version": 1,
  "command": "probe",
  "file": ".xpt/probes/20261126T074618-56c7.jsonl",
  "path": "app/window.py",
  "line": 15,
  "expr": null,
  "when": null,
  "max_hits": 100,
  "exit_code": 0,
  "hits": 1,
  "reached": 1,
  "processes": 1,
  "when_errors": 0,
  "tracers": ["monitoring"],
  "errors": [],
  "records": [
    {
      "pid": 81543,
      "hit": 1,
      "function": "describe",
      "chain": ["unsupported.py:3 in <module>"],
      "values": [
        {"name": "values", "value": [1, 2]},
        {"name": "sock", "unsupported": "<socket.socket>"}
      ]
    }
  ]
}
```

`hits` counts every hit where `--when` was true, including those past `--max-hits`. `reached` counts every time the line ran. `records` holds the recorded hits. Each value is a [value tree](json.md#values), the type of a value xpt cannot record (`unsupported`), or the exception `--expr` raised (`error`). `chain` lists the project frames that called the hit's function, outermost first. `tracers` names how each process traced: `monitoring` on Python 3.12 and later, `settrace` on 3.11.

## Cost

On Python 3.12 and later the probe uses `sys.monitoring`. Each code object costs one callback the first time it runs, and then only the probed line raises an event, so a program runs at nearly full speed until the line is hit. On 3.11 it uses `sys.settrace`, which calls a function on every Python function call. A loop of two million small function calls took about 0.07 s plain and 0.3 to 0.5 s under the probe on 3.11, and about the same under the probe on 3.13 until the probed line ran. Recording a hit imports xpt, about 40 ms once, and serializes the values.

## Files

```text
.xpt/probes/<id>.jsonl
```

Each probe writes one JSON Lines file: a header line with the target and the command, one line per recorded hit, and one line per process with its counts. `.xpt/` is ignored by git, the last five probes are kept, and `xpt clean` deletes them. The values are real values from your program, so treat the files as you would a log.
