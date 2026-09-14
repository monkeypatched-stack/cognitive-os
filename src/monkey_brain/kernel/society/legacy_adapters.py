"""Legacy Runtime Adapters — Runtime Encapsulation Refactor, Phase 5.

LegacyCognitiveRuntime, SimulationRuntime, and ComparatorRuntime (registered
in Kernel.registry as "cognitive"/"simulation"/"comparator") predate the
Planet -> Society -> Actor model and are the real, live handlers behind
/plan, /execute, /simulate, /compare, /query, /knowledge — the majority of
production traffic. Their APIs are request/response compile-and-execute
pipelines (_plan(), execute_cognitive_workload(), run()), not actor-lifecycle
managers, so they don't map onto kernel/society/runtime_api.py::Society's
vocabulary (register_actor, tick_one_actor, send_message, ...) without
inventing behavior that doesn't exist.

Rather than force-fitting a fake Society-protocol implementation, each
adapter here is a thin, explicitly-labeled wrapper: every attribute access
proxies straight through to the wrapped instance via __getattr__/__setattr__,
so calling code sees IDENTICAL behavior — this is a naming/boundary seam, not
a behavior change. The only thing that changes is that call sites now depend
on an adapter type (self-documenting as "legacy"), not the raw runtime
class directly, so a future migration to a true Planet/Society/Actor-native
implementation has one seam to change instead of N call sites.

Both directions must proxy: api/routes/execute.py does
`runtime.execution_graph = execution_graph` before calling
execute_cognitive_workload() — execution_graph is a real @property/setter
pair on CognitiveRuntime backed by a ContextVar (kernel/cognitive_runtime.py),
not a plain attribute. A __getattr__-only adapter would silently accept that
assignment onto its OWN instance dict instead of invoking the wrapped
object's setter, leaving the ContextVar unset and every subsequent read
through the wrapped instance's own methods (which never go through this
adapter) seeing stale/None state — the exact bug __setattr__ below prevents.

Also covers the one non-actor-scoped API surface found bypassing the
hierarchy the same way: api/routes/agents.py's `/agents/{type}/execute` and
`/execute-direct` construct AgentMiddleware (the AgentRuntime alias) directly
— AgentRuntimeAdapter wraps that construction with the same pattern.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "LegacyRuntimeAdapter",
    "LegacyCognitiveRuntimeAdapter",
    "SimulationRuntimeAdapter",
    "ComparatorRuntimeAdapter",
    "AgentRuntimeAdapter",
]


class LegacyRuntimeAdapter:
    """Marks a wrapped legacy runtime as reached only through this adapter
    layer. Transparent passthrough — see module docstring for why this
    doesn't attempt to implement runtime_api.py::Society's exact method set.
    """

    _OWN_ATTRS = frozenset({"_wrapped", "legacy_kind"})

    def __init__(self, wrapped: Any, *, kind: str) -> None:
        # Bypass __setattr__ for the adapter's own two slots — see
        # __setattr__ below for why this can't just be a plain assignment.
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "legacy_kind", kind)

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes not found on the adapter itself
        # (i.e. everything except _wrapped/legacy_kind) — proxies to the
        # real runtime unchanged.
        return getattr(self._wrapped, name)

    def __setattr__(self, name: str, value: Any) -> None:
        # Writes must proxy too (see module docstring's execution_graph
        # example) — only the adapter's own two slots are set locally.
        if name in self._OWN_ATTRS:
            object.__setattr__(self, name, value)
        else:
            setattr(self._wrapped, name, value)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} kind={self.legacy_kind!r} wrapping={self._wrapped!r}>"


class LegacyCognitiveRuntimeAdapter(LegacyRuntimeAdapter):
    """Wraps the LegacyCognitiveRuntime singleton registered as "cognitive"
    — the real handler behind /plan, /execute, /execute/stream, /query,
    /knowledge, and part of /compare."""

    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped, kind="cognitive")


class SimulationRuntimeAdapter(LegacyRuntimeAdapter):
    """Wraps the SimulationRuntime singleton registered as "simulation" —
    the real handler behind /simulate and part of /compare."""

    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped, kind="simulation")


class ComparatorRuntimeAdapter(LegacyRuntimeAdapter):
    """Wraps the ComparatorRuntime singleton registered as "comparator"."""

    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped, kind="comparator")


class AgentRuntimeAdapter(LegacyRuntimeAdapter):
    """Wraps a directly-constructed AgentMiddleware (the AgentRuntime alias)
    for routes that invoke agent execution outside any actor's Planet/
    Society/Actor context (api/routes/agents.py's /execute and
    /execute-direct) — a global agent-execution surface that predates the
    actor hierarchy, analogous to the other three adapters above."""

    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped, kind="agent_runtime")
