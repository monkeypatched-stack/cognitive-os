"""descriptor.py — CapabilityDescriptor: the ISA semantic entry.

Extends the existing CapabilityManifest (which handles discovery/publishing)
with the semantic contract needed for simulation and planning:

    CapabilityManifest  — registry metadata (author, version, tags, checksum)
    CapabilityDescriptor — ISA semantic contract (requires, produces, effects,
                           preconditions, enables, disables, provider bindings)

These are complementary — a capability has both.  The bus uses descriptors;
the registry uses manifests.

YAML schema:
    capability:
      name: CreateWorkOrder
      requires: [Customer, Machine]
      produces: [WorkOrder]
      effects: [WorkOrderCreated]
      preconditions: ["customer.status == Active"]
      enables: [AssignWorkOrder, CancelWorkOrder]
      providers:
        - transport: rest
          endpoint: /api/work-orders
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderBinding:
    """How a capability is physically reached — transport + endpoint + priority.

    Distinct from Peripheral (which manages a connection lifecycle).
    ProviderBinding is static config; Peripheral is a live connection.
    """

    transport: str  # rest | nats | ros2 | grpc | local | kafka | mqtt
    endpoint: str  # REST path, NATS subject, ROS2 topic, local fn name
    priority: int = 0  # higher = preferred when multiple bindings exist
    timeout_ms: float = 5000.0
    retry: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "endpoint": self.endpoint,
            "priority": self.priority,
            "timeout_ms": self.timeout_ms,
            "retry": self.retry,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderBinding":
        return cls(
            transport=d.get("transport", "local"),
            endpoint=d.get("endpoint", ""),
            priority=int(d.get("priority", 0)),
            timeout_ms=float(d.get("timeout_ms", 5000.0)),
            retry=int(d.get("retry", 0)),
        )


@dataclass
class CapabilityDescriptor:
    """ISA semantic entry for a capability.

    What it IS (semantically) — not how it runs.
    The bus resolves which ProviderBinding executes it at runtime.

    Semantic contracts:
        requires     — entity types that must exist before invocation
        produces     — entity types created/updated as output
        effects      — domain events emitted on success
        side_effects — external systems mutated (audit trail)
        preconditions  — symbolic predicates: "customer.status == Active"
        postconditions — assertions verified after execution

    Affordance wiring (feeds L_affordance in SimulationLoss):
        enables  — capabilities unlocked after this succeeds
        disables — capabilities locked after this succeeds
    """

    name: str
    domain: str = ""
    description: str = ""
    requires: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    enables: list[str] = field(default_factory=list)
    disables: list[str] = field(default_factory=list)
    cost_ms: float = 10.0
    confidence: float = 0.9
    providers: list[ProviderBinding] = field(default_factory=list)
    # EPA epistemic operator fields — read by epa_transition() to update K without hardcoding
    knowledge_effects: list[str] = field(default_factory=list)  # "adds:X", "removes:Y"
    confidence_delta: float = 0.0  # expected confidence change per invocation
    expected_utility: float = 1.0  # prior utility estimate (updated by BellmanLearningLoop)

    # ── Affordance helpers ───────────────────────────────────────────────────

    def preferred_provider(self) -> ProviderBinding | None:
        if not self.providers:
            return None
        return max(self.providers, key=lambda p: p.priority)

    def affordance_delta(self, *, positive: bool) -> set[str]:
        return set(self.enables) if positive else set(self.disables)

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "description": self.description,
            "requires": self.requires,
            "produces": self.produces,
            "effects": self.effects,
            "side_effects": self.side_effects,
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
            "enables": self.enables,
            "disables": self.disables,
            "cost_ms": self.cost_ms,
            "confidence": self.confidence,
            "providers": [p.to_dict() for p in self.providers],
            "knowledge_effects": self.knowledge_effects,
            "confidence_delta": self.confidence_delta,
            "expected_utility": self.expected_utility,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityDescriptor":
        return cls(
            name=d["name"],
            domain=d.get("domain", ""),
            description=d.get("description", ""),
            requires=list(d.get("requires", [])),
            produces=list(d.get("produces", [])),
            effects=list(d.get("effects", [])),
            side_effects=list(d.get("side_effects", [])),
            preconditions=list(d.get("preconditions", [])),
            postconditions=list(d.get("postconditions", [])),
            enables=list(d.get("enables", [])),
            disables=list(d.get("disables", [])),
            cost_ms=float(d.get("cost_ms", 10.0)),
            confidence=float(d.get("confidence", 0.9)),
            providers=[ProviderBinding.from_dict(p) for p in d.get("providers", [])],
            knowledge_effects=list(d.get("knowledge_effects", [])),
            confidence_delta=float(d.get("confidence_delta", 0.0)),
            expected_utility=float(d.get("expected_utility", 1.0)),
        )
