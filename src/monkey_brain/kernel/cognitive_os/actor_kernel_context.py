"""ActorKernelContext — the actor-owned half of "kernel" state.

Per-Actor CognitiveOS Isolation refactor (see the repo-wide audit this
follows). Kernel itself (kernel/kernel.py) legitimately stays ONE
process-wide singleton — it owns boot-time, genuinely-global
infrastructure (provider registration, DB/Redis/Mongo connections, the
policy control plane, the exchange network transport). None of that is
actor-specific, and duplicating it per actor would mean reconnecting to
every shared backend service N times for no isolation benefit — exactly
the "Immutable + actor-independent -> MAY be shared" / "Mutable +
genuinely global -> MAY be shared" cases the refactor's own classification
rule calls out.

What Kernel does NOT own, and never did, is any actor-specific EXECUTION
state: which execution this actor is currently running, whether it's been
interrupted, which run_ids belong to it. That state genuinely needs a
home, and previously had none at all (it lived nowhere, or was
reconstructed ad hoc per call). ActorKernelContext is that home — one
instance per actor (constructed by CognitiveOS, never shared), holding
real, actor-owned mutable state, with read-only references to the shared
Kernel-level infrastructure for the rare case actor-facing code needs it.
"""

from __future__ import annotations

from typing import Any


class ActorKernelContext:
    """One per actor. Owns this actor's own execution-scoped kernel state;
    never shared with any other actor's ActorKernelContext."""

    def __init__(self, actor_id: str, tenant_id: str = "default") -> None:
        self.actor_id = actor_id
        self.tenant_id = tenant_id
        self.current_execution_id: str | None = None
        self._interrupts: set[str] = set()
        self._run_ids: list[str] = []

    # ── Execution tracking ───────────────────────────────────────────

    def begin_execution(self, execution_id: str) -> None:
        self.current_execution_id = execution_id
        if execution_id not in self._run_ids:
            self._run_ids.append(execution_id)

    def end_execution(self) -> None:
        self.current_execution_id = None

    @property
    def run_ids(self) -> tuple[str, ...]:
        """Every execution_id/run_id this actor has started, oldest first
        — this actor's own process table view, without reading (or being
        able to read) any other actor's entries out of the shared,
        run_id-keyed ProcessManager/RunStore."""
        return tuple(self._run_ids)

    # ── Interrupts ────────────────────────────────────────────────────

    def interrupt(self, reason: str) -> None:
        self._interrupts.add(reason)

    def clear_interrupt(self, reason: str) -> None:
        self._interrupts.discard(reason)

    @property
    def is_interrupted(self) -> bool:
        return bool(self._interrupts)

    @property
    def interrupts(self) -> frozenset[str]:
        return frozenset(self._interrupts)

    # ── Shared infrastructure access (read-only reference, not owned) ──

    def shared_kernel(self) -> Any:
        """The one process-wide Kernel singleton, for the rare case
        actor-facing code genuinely needs shared infrastructure (e.g. a
        provider lookup) — a reference, not a copy. Returns None if the
        Kernel hasn't booted (e.g. under a bare unit test with no live
        server), same "honest, not fabricated" convention every other
        optional-dependency accessor in this codebase already follows."""
        from src.monkey_brain.kernel.kernel import Kernel

        return Kernel._instance
