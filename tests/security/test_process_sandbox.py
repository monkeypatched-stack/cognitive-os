"""ProcessSandbox — real OS-process isolation for untrusted work.

A crash or infinite loop in the isolated child must NOT take down the host, and a runaway is
killed at the wall-clock limit. Uses a thread-safe start method (forkserver/spawn), so targets
are importable module-level callables.
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.execute.sandbox import ProcessSandbox, SandboxLimits
from src.monkey_brain.kernel.execute import _sandbox_targets as T


def test_isolated_success_returns_result():
    res = ProcessSandbox(SandboxLimits(timeout_seconds=15.0)).run(T.add, 2, 3)
    assert res.success and res.output == 5


def test_isolated_crash_does_not_kill_host():
    res = ProcessSandbox(SandboxLimits(timeout_seconds=15.0)).run(T.crash)
    assert res.success is False and "boom in child" in res.error
    # host is unmistakably alive:
    assert ProcessSandbox(SandboxLimits(timeout_seconds=15.0)).run(T.const42).output == 42


def test_isolated_runaway_is_killed_at_timeout():
    sb = ProcessSandbox(SandboxLimits(timeout_seconds=0.5))
    t = time.monotonic()
    res = sb.run(T.busy_loop)
    dt = time.monotonic() - t
    assert res.success is False and res.error == "timeout_exceeded"
    assert dt < 8.0  # killed, not hung (allow for spawn startup)
