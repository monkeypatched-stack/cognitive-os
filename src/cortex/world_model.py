"""World Model — continuously evolving environment representation.

Maintains:
- Environment State
- Entity State
- Capability State
- Pipeline Performance
- Policy Confidence
- Historical Outcomes
- Resource Availability
- Failure Statistics
- Cost Models
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class EntityState:
    """State of a single entity."""

    entity_id: str = ""
    entity_type: str = ""
    confidence: float = 1.0
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityState:
    """State of a capability."""

    capability_name: str = ""
    accuracy: float = 0.5
    reliability: float = 0.5
    avg_latency_ms: float = 0.0
    failure_rate: float = 0.0
    total_executions: int = 0
    total_failures: int = 0
    cost_per_execution: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineState:
    """State of a pipeline."""

    pipeline_id: str = ""
    success_rate: float = 0.5
    avg_reward: float = 0.0
    avg_cost: float = 0.0
    avg_latency_ms: float = 0.0
    total_executions: int = 0
    total_failures: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class WorldModel:
    """Continuously evolving environment representation.

    Maintains state across all subsystems.
    Supports incremental updates.
    """

    def __init__(self):
        self._entities: dict[str, EntityState] = {}
        self._capabilities: dict[str, CapabilityState] = {}
        self._pipelines: dict[str, PipelineState] = {}
        self._environment: dict[str, Any] = {}
        self._cost_models: dict[str, dict[str, float]] = {}
        self._failure_stats: dict[str, int] = {}

    # --- Entity State ---

    def update_entity(self, entity_id: str, entity_type: str, **metadata: Any) -> None:
        """Fold new facts into an entity's state.

        This used to construct a brand-new EntityState on every call, so each update
        REPLACED the entity outright: prior metadata was discarded (update once with
        line='A', again with status='running', and 'line' was gone) and `confidence` was
        reset to its 1.0 default. The class advertises "Supports incremental updates" —
        merge rather than clobber.
        """
        existing = self._entities.get(entity_id)
        if existing is None:
            self._entities[entity_id] = EntityState(
                entity_id=entity_id,
                entity_type=entity_type,
                metadata=dict(metadata),
            )
            return
        existing.entity_type = entity_type or existing.entity_type
        existing.metadata.update(metadata)
        existing.last_updated = datetime.now(timezone.utc).isoformat()

    def get_entity(self, entity_id: str) -> EntityState | None:
        return self._entities.get(entity_id)

    def get_entities_by_type(self, entity_type: str) -> list[EntityState]:
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    # --- Capability State ---

    def update_capability(
        self,
        name: str,
        success: bool,
        latency_ms: float = 0.0,
        cost: float = 0.0,
    ) -> None:
        if name not in self._capabilities:
            self._capabilities[name] = CapabilityState(capability_name=name)

        cap = self._capabilities[name]
        cap.total_executions += 1
        if not success:
            cap.total_failures += 1

        # Update rolling averages (incremental mean: avg ← avg·(n-1)/n + x/n)
        n = cap.total_executions
        cap.avg_latency_ms = cap.avg_latency_ms * (n - 1) / n + latency_ms / n
        cap.failure_rate = cap.total_failures / n if n > 0 else 0.0
        cap.reliability = 1.0 - cap.failure_rate
        # `success` is the ONLY outcome signal this method receives — there is no
        # ground-truth correctness input — so accuracy is not separately measurable and
        # is the same quantity as reliability. The old `0.5 + 0.5 * reliability` invented
        # a second metric out of the first and compressed it into [0.5, 1.0], so a
        # capability that failed 20 times out of 20 still reported accuracy 0.5 — it could
        # never say a capability was worse than a coin flip. Report what was measured.
        cap.accuracy = cap.reliability
        cap.cost_per_execution = cap.cost_per_execution * (n - 1) / n + cost / n

    def get_capability(self, name: str) -> CapabilityState | None:
        return self._capabilities.get(name)

    # --- Pipeline State ---

    def update_pipeline(
        self,
        pipeline_id: str,
        success: bool,
        reward: float = 0.0,
        cost: float = 0.0,
        latency_ms: float = 0.0,
    ) -> None:
        if pipeline_id not in self._pipelines:
            self._pipelines[pipeline_id] = PipelineState(pipeline_id=pipeline_id)

        pipe = self._pipelines[pipeline_id]
        pipe.total_executions += 1
        if not success:
            pipe.total_failures += 1

        n = pipe.total_executions
        pipe.avg_reward = pipe.avg_reward * (n - 1) / n + reward / n
        pipe.avg_cost = pipe.avg_cost * (n - 1) / n + cost / n
        pipe.avg_latency_ms = pipe.avg_latency_ms * (n - 1) / n + latency_ms / n
        pipe.success_rate = 1.0 - (pipe.total_failures / n if n > 0 else 0.0)

    def get_pipeline(self, pipeline_id: str) -> PipelineState | None:
        return self._pipelines.get(pipeline_id)

    # --- Environment ---

    def update_environment(self, **kwargs: Any) -> None:
        self._environment.update(kwargs)

    def get_environment(self) -> dict[str, Any]:
        return dict(self._environment)

    # --- Cost Model ---

    def update_cost_model(self, component: str, costs: dict[str, float]) -> None:
        self._cost_models[component] = costs

    def get_cost_model(self, component: str) -> dict[str, float]:
        return self._cost_models.get(component, {})

    # --- Failure Stats ---

    def record_failure(self, component: str) -> None:
        self._failure_stats[component] = self._failure_stats.get(component, 0) + 1

    def get_failure_stats(self) -> dict[str, int]:
        return dict(self._failure_stats)

    def summary(self) -> dict:
        return {
            "entities": len(self._entities),
            "capabilities": len(self._capabilities),
            "pipelines": len(self._pipelines),
            "environment_keys": len(self._environment),
            "cost_models": len(self._cost_models),
            "failure_components": len(self._failure_stats),
        }
