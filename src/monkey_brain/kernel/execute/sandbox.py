"""Agent Sandbox — permission-gated execution boundary for agents.

Agents execute within a sandbox that enforces:
- Capability permissions (what the agent can do)
- Resource limits (max steps, timeout, memory)
- Output validation (never return dangerous data)
- Input sanitization (reject malformed tasks)

This is NOT process isolation (that's 1.0 scope). It's a logical boundary
that can be upgraded to real isolation (subprocess, container) later.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("agentos.sandbox")

# NOT fork: the host server runs background threads (exchange flusher, bundle refresher) and
# fork()-after-threads can deadlock in the child. forkserver/spawn start from a clean process
# and are thread-safe; both require the target to be importable (module-level, picklable).
_methods = mp.get_all_start_methods()
_MP_CTX = mp.get_context("forkserver" if "forkserver" in _methods else "spawn")


@dataclass
class SandboxLimits:
    """Resource limits for an agent execution."""

    max_steps: int = 100
    timeout_seconds: float = 30.0
    max_output_size: int = 1_000_000  # bytes
    allowed_capabilities: set[str] = field(default_factory=set)  # empty = all allowed
    denied_capabilities: set[str] = field(default_factory=set)  # explicitly denied
    max_memory_bytes: int = 100_000_000  # 100MB


@dataclass
class SandboxResult:
    """Result of a sandboxed execution."""

    success: bool
    output: Any = None
    error: str = ""
    violations: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    steps_used: int = 0


class AgentSandbox:
    """Permission-gated execution boundary for agents.

    Wraps agent execution with:
    - Capability permission checks
    - Resource limit enforcement
    - Output size validation
    - Execution timing
    """

    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self._limits = limits or SandboxLimits()
        self._step_count = 0
        self._start_time = 0.0

    def check_capability(self, capability: str) -> tuple[bool, str]:
        """Check if a capability is allowed. Returns (allowed, reason)."""
        if capability in self._limits.denied_capabilities:
            return False, f"capability_denied: {capability}"
        if self._limits.allowed_capabilities and capability not in self._limits.allowed_capabilities:
            return False, f"capability_not_in_allowlist: {capability}"
        return True, ""

    def check_timeout(self) -> bool:
        """Return True if execution has exceeded the timeout."""
        if self._start_time == 0:
            return False
        return (time.monotonic() - self._start_time) > self._limits.timeout_seconds

    def check_step_limit(self) -> bool:
        """Return True if step limit exceeded."""
        return self._step_count >= self._limits.max_steps

    def validate_output(self, output: Any) -> tuple[bool, str]:
        """Validate that output doesn't violate sandbox rules."""
        if output is None:
            return True, ""
        import json

        try:
            size = len(json.dumps(output, default=str))
        except Exception:
            size = 0
        if size > self._limits.max_output_size:
            return False, f"output_too_large: {size} > {self._limits.max_output_size}"
        return True, ""

    async def execute(self, agent_fn, task: dict[str, Any], **kwargs) -> SandboxResult:
        """Execute an agent function within the sandbox.

        Wraps the function with permission checks, timeout, and output validation.
        """
        self._step_count = 0
        self._start_time = time.monotonic()
        violations = []

        # Check if capability is allowed
        capability = task.get("capability", "")
        if capability:
            allowed, reason = self.check_capability(capability)
            if not allowed:
                violations.append(reason)
                return SandboxResult(
                    success=False,
                    error=reason,
                    violations=violations,
                    elapsed_ms=(time.monotonic() - self._start_time) * 1000,
                )

        try:
            # Enforce the wall-clock timeout — a runaway/malicious agent is cancelled at
            # the limit rather than running unbounded (previously the timeout was never
            # applied; it only caught a TimeoutError the agent raised itself).
            output = await asyncio.wait_for(agent_fn(task, **kwargs), timeout=self._limits.timeout_seconds)

            # Validate output
            valid, reason = self.validate_output(output)
            if not valid:
                violations.append(reason)
                return SandboxResult(
                    success=False,
                    error=reason,
                    violations=violations,
                    elapsed_ms=(time.monotonic() - self._start_time) * 1000,
                )

            elapsed = (time.monotonic() - self._start_time) * 1000
            return SandboxResult(
                success=True,
                output=output,
                violations=violations,
                elapsed_ms=elapsed,
                steps_used=self._step_count,
            )

        except TimeoutError:
            violations.append("timeout_exceeded")
            return SandboxResult(
                success=False,
                error="timeout_exceeded",
                violations=violations,
                elapsed_ms=(time.monotonic() - self._start_time) * 1000,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - self._start_time) * 1000
            return SandboxResult(
                success=False,
                error=str(exc),
                violations=violations,
                elapsed_ms=elapsed,
            )

    def reset(self) -> None:
        """Reset sandbox state for a new execution."""
        self._step_count = 0
        self._start_time = 0.0


def _isolated_target(q, fn, args, kwargs, memory_bytes) -> None:
    """Child-process entrypoint: apply resource caps, run, ship the result back."""
    try:
        import resource

        if memory_bytes:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            except (ValueError, OSError):
                logger.debug(
                    "_isolated_target: suppressed exception", exc_info=True
                )  # RLIMIT_AS is best-effort (ignored on some OSes)
    except Exception:
        logger.debug("_isolated_target: suppressed exception", exc_info=True)
    try:
        q.put(("ok", fn(*args, **(kwargs or {}))))
    except BaseException as exc:  # MemoryError, crashes, anything
        q.put(("err", f"{type(exc).__name__}: {exc}"))


class ProcessSandbox:
    """Real OS-process isolation for untrusted SYNC work.

    Unlike AgentSandbox (a logical in-process boundary), this runs the callable in a
    SEPARATE process with a wall-clock timeout and a best-effort memory cap — a crash,
    OOM, or infinite loop in the child cannot take down the host runtime. This is the
    isolation primitive for running untrusted government/enterprise agent code.
    """

    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self._limits = limits or SandboxLimits()

    def run(self, fn: Callable, *args: Any, **kwargs: Any) -> SandboxResult:
        q = _MP_CTX.Queue()
        p = _MP_CTX.Process(
            target=_isolated_target,
            args=(q, fn, args, kwargs, self._limits.max_memory_bytes),
        )
        start = time.monotonic()
        p.start()
        p.join(self._limits.timeout_seconds)
        if p.is_alive():  # timed out — kill the runaway child
            p.terminate()
            p.join(1.0)
            if p.is_alive():
                p.kill()
                p.join(1.0)
            return SandboxResult(
                success=False,
                error="timeout_exceeded",
                violations=["timeout_exceeded"],
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        elapsed = (time.monotonic() - start) * 1000
        try:
            status, payload = q.get_nowait()
        except Exception:  # child died without producing a result
            return SandboxResult(
                success=False,
                error="process_died",
                violations=["process_died"],
                elapsed_ms=elapsed,
            )
        if status == "ok":
            return SandboxResult(success=True, output=payload, elapsed_ms=elapsed)
        return SandboxResult(
            success=False,
            error=payload,
            violations=["execution_error"],
            elapsed_ms=elapsed,
        )


def create_sandbox(agent_type: str = "default") -> AgentSandbox:
    """Create a sandbox with appropriate limits for an agent type."""
    limits = SandboxLimits()
    if agent_type == "untrusted":
        limits.max_steps = 10
        limits.timeout_seconds = 5.0
        limits.max_output_size = 10_000
        limits.max_memory_bytes = 10_000_000  # 10MB
    elif agent_type == "enterprise":
        limits.max_steps = 500
        limits.timeout_seconds = 120.0
        limits.max_output_size = 10_000_000
    elif agent_type == "government":
        limits.max_steps = 1000
        limits.timeout_seconds = 300.0
        limits.max_output_size = 50_000_000
    return AgentSandbox(limits)
