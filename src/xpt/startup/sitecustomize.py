"""Installed by `xpt run` through PYTHONPATH, so every Python process the command starts
prints uncaught exceptions in xpt's compact shape.

It imports nothing from xpt until an exception reaches a hook, so a process that does not
crash pays only for this file. It first imports the `sitecustomize` it shadows, if any, as
Python would have, and installs its hooks over any that one set. Without an importable
xpt the hooks print Python's own traceback.
"""

import os
import sys

try:
    _CWD = os.getcwd()
except OSError:
    _CWD = os.curdir


def _chain() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != here]
    mine = sys.modules.pop(__name__)
    try:
        import sitecustomize  # noqa: F401  # the one this file shadows
    except ImportError as error:
        if error.name != "sitecustomize":
            raise
    finally:
        # The import system reads this file's module back from sys.modules after it runs.
        sys.modules.setdefault(__name__, mine)


def _hooks():
    try:
        from xpt.traceback import Hooks

        return Hooks.start(_CWD)
    except Exception:
        return None


def _install() -> None:
    import _thread

    python_hook = sys.excepthook
    python_threadhook = _thread._excepthook

    def excepthook(kind, error, tb):
        hooks = _hooks()
        if hooks is None:
            python_hook(kind, error, tb)
        else:
            hooks.excepthook(kind, error, tb)

    def threadhook(args):
        hooks = _hooks()
        if hooks is None:
            python_threadhook(args)
        else:
            hooks.threadhook(args)

    sys.excepthook = excepthook
    # threading copies _thread._excepthook when it is first imported, so setting it here
    # covers threads without importing threading at startup.
    _thread._excepthook = threadhook
    if "threading" in sys.modules:
        sys.modules["threading"].excepthook = threadhook  # ty: ignore[missing-attribute]


_chain()
if os.environ.get("XPT_TRACEBACK"):
    _install()
