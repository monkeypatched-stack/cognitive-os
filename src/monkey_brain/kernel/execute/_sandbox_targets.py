"""Importable target callables for ProcessSandbox tests/demos.

With a thread-safe start method (forkserver/spawn) the sandbox target must be importable by
qualified name (picklable) — closures and lambdas won't cross the process boundary. Keep this
module stdlib-only so child startup stays fast.
"""

from __future__ import annotations


def add(a, b):
    return a + b


def const42():
    return 42


def crash():
    raise RuntimeError("boom in child")


def busy_loop():
    while True:  # runaway — must be killed at the sandbox timeout
        pass
