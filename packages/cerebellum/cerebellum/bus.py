"""bus.py — CapabilityBus: transport-agnostic capability invocation.

The bus is NOT a replacement for NANDA (cross-network agent discovery) or
OpenClaw (agent-to-agent execution).  It is the LOCAL execution surface for
the capability graph within a single process or mesh node.

    NANDA       — discover remote agents across the network
    OpenClaw    — execute agent-to-agent across the network
    CapabilityBus — invoke local capabilities via descriptor + transport binding

Invocation flow:
    1. Look up CapabilityDescriptor by name
    2. Check preconditions against world_state
    3. Resolve the best ProviderBinding by transport priority
    4. Dispatch to the registered transport handler
    5. Record effects into world_state
    6. Return CapabilityResult

Transport handlers are registered as async callables:
    bus.register_transport("rest", rest_handler)
    bus.register_transport("local", local_handler)
    bus.register_transport("nats", nats_handler)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from cerebellum.descriptor import CapabilityDescriptor, ProviderBinding

logger = logging.getLogger("cerebellum.bus")

_TRANSPORT_PRIORITY = {
    "local": 100,
    "rest": 80,
    "grpc": 75,
    "nats": 70,
    "kafka": 65,
    "ros2": 60,
    "mqtt": 50,
    "serverless": 40,
}


@dataclass
class CapabilityResult:
    """Standardised result envelope from any transport."""

    name: str
    success: bool
    produced: dict[str, Any] = field(default_factory=dict)
    events_emitted: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "produced": self.produced,
            "events_emitted": self.events_emitted,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
        }


# Transport handler signature: async fn(descriptor, binding, inputs, context) -> dict
TransportHandler = Callable[
    [CapabilityDescriptor, ProviderBinding, dict[str, Any], dict[str, Any]],
    Any,
]


class CapabilityBus:
    """Transport-agnostic local capability execution surface.

    Usage:
        bus = CapabilityBus()
        bus.load(descriptor)

        # register a local handler
        async def handle_create(desc, binding, inputs, ctx):
            return {"work_order_id": "WO-001"}
        bus.register_transport("local", handle_create)

        result = await bus.invoke("CreateWorkOrder", {"customer_id": "C001"})
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, CapabilityDescriptor] = {}
        self._transports: dict[str, TransportHandler] = {}

    # ── Population ────────────────────────────────────────────────────────────

    def load(self, desc: CapabilityDescriptor) -> "CapabilityBus":
        self._descriptors[desc.name] = desc
        return self

    def load_many(self, descs: list[CapabilityDescriptor]) -> "CapabilityBus":
        for d in descs:
            self.load(d)
        return self

    def register_transport(self, transport: str, handler: TransportHandler) -> "CapabilityBus":
        self._transports[transport] = handler
        return self

    # ── Invocation ────────────────────────────────────────────────────────────

    async def invoke(
        self,
        name: str,
        inputs: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
        skip_preconditions: bool = False,
    ) -> CapabilityResult:
        inputs = inputs or {}
        context = context or {}
        ws = context.get("world_state", {})

        desc = self._descriptors.get(name)
        if desc is None:
            return CapabilityResult(name=name, success=False, error=f"capability '{name}' not loaded in bus")

        if not skip_preconditions:
            failed = self._failed_precondition(desc, ws)
            if failed:
                return CapabilityResult(name=name, success=False, error=f"precondition failed: {failed}")

        binding = self._resolve_binding(desc)
        if binding is None:
            return CapabilityResult(name=name, success=False, error=f"no transport handler for '{name}'")

        handler = self._transports.get(binding.transport)
        if handler is None:
            return CapabilityResult(
                name=name,
                success=False,
                error=f"transport '{binding.transport}' not registered",
            )

        start = time.monotonic()
        try:
            raw = handler(desc, binding, inputs, context)
            if asyncio.iscoroutine(raw):
                raw = await raw
            produced = raw if isinstance(raw, dict) else {}
            result = CapabilityResult(
                name=name,
                success=True,
                produced=produced,
                events_emitted=list(desc.effects),
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            result = CapabilityResult(
                name=name,
                success=False,
                error=str(exc),
                latency_ms=(time.monotonic() - start) * 1000,
            )

        if result.success:
            for event in desc.effects:
                ws.setdefault("events", []).append(event)
            ws.setdefault("produced", {}).update(result.produced)

        return result

    async def invoke_many(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        context: dict[str, Any] | None = None,
        concurrency: int = 4,
    ) -> list[CapabilityResult]:
        """Invoke multiple capabilities with bounded concurrency (for GoT branches)."""
        context = context or {}
        sem = asyncio.Semaphore(concurrency)

        async def _one(name: str, inp: dict) -> CapabilityResult:
            async with sem:
                return await self.invoke(name, inp, context=context)

        return await asyncio.gather(*[_one(n, i) for n, i in calls])

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _failed_precondition(self, desc: CapabilityDescriptor, ws: dict) -> str | None:
        for pred in desc.preconditions:
            if not self._eval(pred, ws):
                return pred
        return None

    def _resolve_binding(self, desc: CapabilityDescriptor) -> ProviderBinding | None:
        candidates = [b for b in desc.providers if b.transport in self._transports]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda b: (b.priority, _TRANSPORT_PRIORITY.get(b.transport, 0)),
        )

    def _eval(self, predicate: str, ws: dict) -> bool:
        for op in ("==", "!=", ">=", "<=", ">", "<"):
            if op in predicate:
                lhs, rhs = predicate.split(op, 1)
                lv = self._resolve(lhs.strip(), ws)
                rv = self._coerce(rhs.strip())
                try:
                    if op == "==":
                        return lv == rv
                    if op == "!=":
                        return lv != rv
                    if op == ">":
                        return lv > rv  # type: ignore[operator]
                    if op == ">=":
                        return lv >= rv  # type: ignore[operator]
                    if op == "<":
                        return lv < rv  # type: ignore[operator]
                    if op == "<=":
                        return lv <= rv  # type: ignore[operator]
                except Exception:
                    return True
        return True

    @staticmethod
    def _resolve(path: str, state: dict) -> Any:
        cur: Any = state
        for p in path.split("."):
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur

    @staticmethod
    def _coerce(v: str) -> Any:
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v.strip("'\"")
