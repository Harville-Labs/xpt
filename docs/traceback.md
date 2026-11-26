# Tracebacks outside pytest

Scripts, CLIs, servers, and `python -c` print Python's full traceback when they crash. It holds every frame in installed packages, a `During handling of the above exception` block per chained exception, and a thousand lines for a recursion error. xpt can print the same short shape the [pytest report](pytest.md) uses instead.

```
ValueError: invalid literal for int() with base 10: 'x'
  at app/load.py:6 in read_row
  > return fakelib.parse(count)
  via fakelib/core.py
  from app/load.py:12 in load, library.py:4 in <module>
  locals:
    row = {"count": "x"}
    count = "x"
```

## Turning it on

Run any command through `xpt run`:

```bash
xpt run -- python app.py
xpt run -- uvicorn app:main
xpt run -- python -m mypackage --flag
```

Every Python process the command starts prints compact tracebacks, including subprocesses, as long as it can import xpt. Its exit code, stdout, and where the traceback is written are the same as without `xpt run`.

A program can also call the hook itself:

```python
import xpt.traceback

xpt.traceback.install()
```

`install()` only installs when `XPT_TRACEBACK` is set, so it can stay in a program's entry point: people get Python's traceback, and an agent that sets `XPT_TRACEBACK=1` gets the compact one. `install("1")` installs regardless of the variable. It returns whether it installed.

| `XPT_TRACEBACK` | Effect |
|---|---|
| unset, `0`, `off`, `full` | Python's own traceback. |
| `verbose` | The compact traceback with the `-v` limits. |
| anything else, such as `1` | The compact traceback. |

`xpt run -v` raises the limits, and `xpt run -vv` keeps Python's full traceback for that command.

## What it prints

- `Type: message`, cut to 200 characters. Builtin exceptions print their bare name, others their module and name, as in the pytest report.
- `at` names the deepest frame in a project file, with its source line after `> `. The project is the nearest directory up from where the process started that holds `pyproject.toml` or `.git`, or the start directory when there is none. Files under `site-packages`, `dist-packages`, the standard library, and the running Python's script directory are not project files. Code typed with `-c` or at a prompt is.
- `via` names the first library module the deepest project frame called into, as `pkg/module.py` relative to the `sys.path` entry it came from. The library's own frames are left out.
- `from` lists the project frames that called it, nearest first. A frame or a cycle of up to 8 frames that repeats, as recursion leaves them, is one entry with its count, such as `app/deep.py:2 in depth (997 times)`. Beyond 4 entries, the outermost and the 3 nearest are kept.
- Each chained exception is one line. `caused by` follows `raise X from Y`, and `during` follows an exception raised inside an `except` block. `raise X from None` ends the chain. Each line describes the exception on the line before it. An exception group lists each member as a `member` line. Past 10 such lines, the rest are counted.
- `locals` prints the deepest project frame's local variables as one-line value trees, up to 10 of them and 80 characters each. Modules, functions, classes, and dunder names are left out. A value xpt cannot record prints as its type, such as `<socket.socket>`, never as a `repr` with a memory address.
- A thread's exception starts with `Exception in thread <name>:`, as Python's does.

If formatting a traceback fails, xpt prints Python's traceback followed by a one-line notice.

## Limits

When the compact traceback does not show something, Python's still can: `xpt run -vv`, or `XPT_TRACEBACK=full`. `python -I` and `python -E` ignore `PYTHONPATH`, so `xpt run` cannot reach them, and a program that sets its own `sys.excepthook` keeps it.
