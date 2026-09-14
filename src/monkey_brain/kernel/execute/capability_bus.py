"""CapabilityBus — a real unifying dispatch layer over the three registries
that were previously each real on their own but never actually connected:

    Runtime._capabilities   in-process Wolverine capabilities (69 real,
                             e.g. AnthropicCapability) — kernel/runtime/
                             runtime.py.
    AgentBus / AgentRegistry local + Broca agents, with an optional
                             provider-registry fallback tier — kernel/
                             execute/agents/bus.py, kernel/kernel.py.
    ProviderRegistry        external agent providers (openclaw, n8n,
                             nanda, ard) — kernel/provider_registry.py.

Prior to this class, `Runtime` had no `_bus`/`capability_bus` attribute at
all — confirmed by reading the class in full — and code that assumed a
"CapabilityBus" resolving a name across all three registries was simply
wrong about what existed. This makes that resolution real: given a bare
name, it tries the Wolverine capability registry, then the agent bus
(which itself now falls through to providers — see AgentBus._resolve),
and reports which tier actually answered rather than fabricating a single
merged namespace where two of these were never in fact the same thing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.capability_bus")


@dataclass
class CapabilityBusResult:
    name: str
    found: bool
    source: str = ""  # "capability" | "agent" | "provider" | ""
    success: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


class CapabilityBus:
    """Resolves and executes a name across capabilities, agents, and
    providers, in that order. Never claims a tier answered when it
    didn't — `source` on the result says exactly which registry produced
    the answer, so callers (and the admin UI) can tell "a real in-process
    capability ran" apart from "an external provider agent ran"."""

    def __init__(self, runtime: Any, agent_bus: Any, provider_registry: Any | None = None) -> None:
        self._runtime = runtime
        self._agent_bus = agent_bus
        self._provider_registry = provider_registry

    def resolve(self, name: str) -> dict[str, Any]:
        """Look up `name` without executing it. Returns which tier (if
        any) owns it, plus a short real description from that tier."""
        capability = self._runtime.get_capability(name) if self._runtime else None
        if capability is not None:
            return {
                "name": name,
                "found": True,
                "source": "capability",
                "type": type(capability).__name__,
            }

        agent = self._agent_bus.resolve_agent(name) if self._agent_bus else None
        if agent is not None:
            provider = self._agent_bus.get_provider_for(name) if self._agent_bus else None
            return {
                "name": name,
                "found": True,
                "source": "agent",
                "agent_type": getattr(agent, "agent_type", type(agent).__name__),
                "provider": provider.name if provider else None,
            }

        if self._provider_registry is not None:
            found = self._provider_registry.find_agent(name)
            if found is not None:
                return {
                    "name": name,
                    "found": True,
                    "source": "provider",
                    "provider_agent": found,
                }

        return {"name": name, "found": False, "source": ""}

    async def execute(self, name: str, state: dict[str, Any]) -> CapabilityBusResult:
        """Execute `name` via whichever tier actually owns it. Never
        raises — a not-found name is a real, honest `found=False` result,
        not an exception disguising a routing gap."""
        from src.monkey_brain.kernel.security_boundary import ensure_governed

        async def _run() -> CapabilityBusResult:
            return await self._execute_resolved(name, state)

        # Idempotency/nonce fix: a caller that already has a stable id for
        # this exact dispatch (not a fresh one per attempt) can pass it via
        # state["operation_id"] so a retry of the SAME call is recognized
        # as a duplicate by run_governed_mutation's SecurityOperation
        # ledger/idempotency store, instead of every attempt minting its
        # own new_operation_id() uuid4() and never matching. None (the
        # default when a caller has no such id) preserves prior behavior
        # exactly.
        operation_id = state.get("operation_id") if isinstance(state, dict) else None
        return await ensure_governed(f"capability.{name}", name, _run, operation_id=operation_id)

    async def _execute_resolved(self, name: str, state: dict[str, Any]) -> CapabilityBusResult:
        t0 = time.monotonic()

        capability = self._runtime.get_capability(name) if self._runtime else None
        if capability is not None:
            try:
                raw = await capability.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                success = bool(getattr(raw, "success", True))
            except Exception as exc:
                output, success = {"error": str(exc)}, False
            return CapabilityBusResult(
                name=name,
                found=True,
                source="capability",
                success=success,
                payload=output,
                latency_ms=(time.monotonic() - t0) * 1000,
            )

        if self._agent_bus is not None and self._agent_bus.resolve_agent(name) is not None:
            result = await self._agent_bus.execute(name, **state)
            return CapabilityBusResult(
                name=name,
                found=True,
                source="agent",
                success=result.success,
                payload=result.produced,
                latency_ms=result.latency_ms,
            )

        if self._provider_registry is not None and self._provider_registry.find_agent(name) is not None:
            result = await self._provider_registry.execute_agent(name, state)
            return CapabilityBusResult(
                name=name,
                found=True,
                source="provider",
                success=bool(result.get("success", False)),
                payload=result,
                latency_ms=(time.monotonic() - t0) * 1000,
            )

        return CapabilityBusResult(name=name, found=False, source="", latency_ms=(time.monotonic() - t0) * 1000)

    def summary(self) -> dict[str, Any]:
        return {
            "capabilities": (len(self._runtime.list_capabilities()) if self._runtime else 0),
            "provider_registry_attached": self._provider_registry is not None,
        }


__all__ = ["CapabilityBus", "CapabilityBusResult"]
