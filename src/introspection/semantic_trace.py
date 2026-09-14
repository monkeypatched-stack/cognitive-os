"""Semantic traces — cognitive-layer observability events.

Each layer of the cognitive OS emits typed events rather than raw strings.
This module owns the event schemas for every observability layer:

  IntentEvent        — raw language → classified intent + confidence
  GoalEvent          — goal formed, routed, completed, or failed
  PipelineStepEvent  — one step in an executing capability pipeline
  PipelineGraph      — DAG of steps built incrementally; detects bottlenecks + failure chains
  AgentReasonEvent   — perceive→reason→act cycle with timing
  AgentReflectEvent  — reflection triggered (self-correction or uncertainty)
  MemoryAccessEvent  — read/write to short-term, long-term, or episodic memory
  WorldModelEvent    — prediction vs actual; drift detection
  GovernanceEvent    — policy decision, trust score, provenance chain, obligations
  LearningEvent      — Q-value update; convergence tracking; exploration/exploitation ratio
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_WINDOW = 500  # rolling-window size for all event lists


# ---------------------------------------------------------------------------
# Intent layer
# ---------------------------------------------------------------------------


@dataclass
class IntentEvent:
    raw_text: str
    classified_intent: str
    confidence: float
    goal_id: str = ""
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "intent",
            "raw_text": self.raw_text[:200],
            "classified_intent": self.classified_intent,
            "confidence": round(self.confidence, 4),
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Goal layer
# ---------------------------------------------------------------------------


@dataclass
class GoalEvent:
    goal_id: str
    goal_text: str
    status: str  # formed | routed | executing | completed | failed | abandoned
    parent_goal_id: str = ""
    pipeline_id: str = ""
    latency_ms: float = 0.0
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "goal",
            "goal_id": self.goal_id,
            "parent_goal_id": self.parent_goal_id,
            "goal_text": self.goal_text[:300],
            "status": self.status,
            "pipeline_id": self.pipeline_id,
            "latency_ms": round(self.latency_ms, 2),
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Pipeline layer — per-step events + execution graph
# ---------------------------------------------------------------------------


@dataclass
class PipelineStepEvent:
    pipeline_id: str
    step_name: str
    capability: str
    agent_type: str = ""
    status: str = "ok"  # ok | error | skipped
    latency_ms: float = 0.0
    tokens_used: int = 0
    error: str = ""
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "pipeline",
            "pipeline_id": self.pipeline_id,
            "step_name": self.step_name,
            "capability": self.capability,
            "agent_type": self.agent_type,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 2),
            "tokens_used": self.tokens_used,
            "error": self.error[:200] if self.error else "",
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


@dataclass
class _GraphNode:
    step_name: str
    capability: str
    latency_ms: float
    status: str
    tokens: int


class PipelineGraph:
    """Incremental execution graph for a single pipeline run.

    Tracks steps, their order, latency distribution, and propagation of failures.
    Built by Runtime as steps execute; read by the pipeline dashboard.
    """

    def __init__(self, pipeline_id: str, goal: str = "") -> None:
        self.pipeline_id = pipeline_id
        self.goal = goal
        self.created_at = _now()
        self._nodes: list[_GraphNode] = []
        self._edges: list[tuple[str, str]] = []  # (from_step, to_step)
        self._prev: str | None = None

    def add_step(
        self,
        step_name: str,
        capability: str,
        status: str,
        latency_ms: float,
        tokens: int = 0,
    ) -> None:
        node = _GraphNode(step_name, capability, latency_ms, status, tokens)
        self._nodes.append(node)
        if self._prev:
            self._edges.append((self._prev, step_name))
        self._prev = step_name

    def bottlenecks(self) -> list[dict[str, Any]]:
        """Steps whose latency exceeds 2× the median latency."""
        latencies = [n.latency_ms for n in self._nodes if n.latency_ms > 0]
        if not latencies:
            return []
        med = median(latencies)
        threshold = med * 2.0
        return [
            {"step": n.step_name, "latency_ms": n.latency_ms, "median_ms": med}
            for n in self._nodes
            if n.latency_ms > threshold
        ]

    def failure_chain(self) -> list[str]:
        """Steps that failed, in execution order."""
        return [n.step_name for n in self._nodes if n.status == "error"]

    def to_dict(self) -> dict[str, Any]:
        total_ms = sum(n.latency_ms for n in self._nodes)
        return {
            "pipeline_id": self.pipeline_id,
            "goal": self.goal[:200],
            "created_at": self.created_at,
            "step_count": len(self._nodes),
            "total_latency_ms": round(total_ms, 2),
            "nodes": [
                {
                    "step": n.step_name,
                    "capability": n.capability,
                    "status": n.status,
                    "latency_ms": round(n.latency_ms, 2),
                    "tokens": n.tokens,
                }
                for n in self._nodes
            ],
            "edges": [{"from": f, "to": t} for f, t in self._edges],
            "bottlenecks": self.bottlenecks(),
            "failure_chain": self.failure_chain(),
        }


# ---------------------------------------------------------------------------
# Agent layer
# ---------------------------------------------------------------------------


@dataclass
class AgentReasonEvent:
    agent_type: str
    perception: dict[str, Any]
    decision: dict[str, Any]
    latency_ms: float
    goal_id: str = ""
    pipeline_id: str = ""
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "agent",
            "semantic_type": "reasoning",
            "agent_type": self.agent_type,
            "perception_keys": list(self.perception.keys()),
            "decision_keys": list(self.decision.keys()),
            "compliant": self.decision.get("compliant"),
            "violations": self.decision.get("violations", []),
            "latency_ms": round(self.latency_ms, 2),
            "goal_id": self.goal_id,
            "pipeline_id": self.pipeline_id,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentReflectEvent:
    agent_type: str
    reflection: str
    triggered_by: str  # uncertainty | contradiction | failure | periodic
    goal_id: str = ""
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "agent",
            "semantic_type": "reflection",
            "agent_type": self.agent_type,
            "reflection": self.reflection[:500],
            "triggered_by": self.triggered_by,
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


@dataclass
class MemoryAccessEvent:
    agent_type: str
    memory_type: str  # short_term | long_term | episodic | working
    operation: str  # read | write | evict
    key: str
    hit: bool = True
    latency_ms: float = 0.0
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "agent",
            "semantic_type": "memory",
            "agent_type": self.agent_type,
            "memory_type": self.memory_type,
            "operation": self.operation,
            "key": self.key[:100],
            "hit": self.hit,
            "latency_ms": round(self.latency_ms, 2),
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# World Model layer
# ---------------------------------------------------------------------------


@dataclass
class WorldModelEvent:
    """Prediction vs actual observation. Drives drift detection."""

    simulation_id: str
    entity: str
    predicted: dict[str, Any]
    actual: dict[str, Any]
    residual_vector: dict[str, float]  # {"latency_delta": ms, "token_error": int, "path_divergence": 0–1}
    drift_score: float  # 0–1; higher = more drift from expected world state
    last_real_trace_timestamp: float = field(default_factory=time.time)
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def calculate_regional_discount(self) -> float:
        """Discount for this entity region based on path divergence.

        path_divergence 1.0 → discount 0.2 (high structural uncertainty).
        path_divergence 0.0 → discount 1.0 (full confidence in the region).
        """
        return 1.0 - (self.residual_vector.get("path_divergence", 0.0) * 0.8)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "world_model",
            "simulation_id": self.simulation_id,
            "entity": self.entity,
            "residual_vector": self.residual_vector,
            "regional_discount": round(self.calculate_regional_discount(), 4),
            "drift_score": round(self.drift_score, 4),
            "predicted_keys": list(self.predicted.keys()),
            "actual_keys": list(self.actual.keys()),
            "last_real_trace_timestamp": self.last_real_trace_timestamp,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Governance layer
# ---------------------------------------------------------------------------


@dataclass
class GovernanceEvent:
    """A single policy evaluation — the atomic unit of governance observability."""

    policy_path: str
    decision: bool  # allow / deny
    principal: str  # sub / SPIFFE ID
    trust_score: float  # 0–1; derived from mTLS + reliability + audit history
    obligations: list[str]
    provenance_chain: list[str]  # ordered pipeline/step/agent trail
    audit_ref: str = ""  # links to AuditRecord.event_id
    pipeline_id: str = ""
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "governance",
            "policy_path": self.policy_path,
            "decision": "allow" if self.decision else "deny",
            "principal": self.principal[:150],
            "trust_score": round(self.trust_score, 4),
            "obligations": self.obligations,
            "provenance_depth": len(self.provenance_chain),
            "provenance_chain": self.provenance_chain,
            "audit_ref": self.audit_ref,
            "pipeline_id": self.pipeline_id,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Learning layer
# ---------------------------------------------------------------------------


@dataclass
class LearningEvent:
    """A single Q-value update from the Bellman policy."""

    capability: str
    state_key: str
    q_before: float
    q_after: float
    reward: float
    td_error: float  # |q_after - q_before|; proxy for convergence rate
    exploration: bool  # True = random action taken; False = exploitation
    episode: int = 0
    trace_id: str = ""
    timestamp: str = field(default_factory=_now)

    @property
    def converging(self) -> bool:
        return abs(self.td_error) < 0.01

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "learning",
            "capability": self.capability,
            "state_key": self.state_key[:100],
            "q_before": round(self.q_before, 6),
            "q_after": round(self.q_after, 6),
            "reward": round(self.reward, 4),
            "td_error": round(self.td_error, 6),
            "exploration": self.exploration,
            "converging": self.converging,
            "episode": self.episode,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# SemanticEventStore — rolling window store for all event types
# ---------------------------------------------------------------------------


class SemanticEventStore:
    """Bounded rolling-window store for all semantic observability events.

    Each layer uses a separate deque(maxlen=_WINDOW) so one high-frequency layer
    (e.g., memory accesses) cannot starve others.
    """

    def __init__(self, window: int = _WINDOW) -> None:
        self._window = window
        self.intents: deque[IntentEvent] = deque(maxlen=window)
        self.goals: deque[GoalEvent] = deque(maxlen=window)
        self.steps: deque[PipelineStepEvent] = deque(maxlen=window)
        self.graphs: dict[str, PipelineGraph] = {}  # pipeline_id → graph
        self.reasoning: deque[AgentReasonEvent] = deque(maxlen=window)
        self.reflections: deque[AgentReflectEvent] = deque(maxlen=window)
        self.memory: deque[MemoryAccessEvent] = deque(maxlen=window)
        self.world: deque[WorldModelEvent] = deque(maxlen=window)
        self.governance: deque[GovernanceEvent] = deque(maxlen=window)
        self.learning: deque[LearningEvent] = deque(maxlen=window)

    # Convenience: get/create graph for a pipeline
    def graph_for(self, pipeline_id: str, goal: str = "") -> PipelineGraph:
        if pipeline_id not in self.graphs:
            self.graphs[pipeline_id] = PipelineGraph(pipeline_id, goal)
            # Cap number of stored graphs
            if len(self.graphs) > self._window:
                oldest = next(iter(self.graphs))
                del self.graphs[oldest]
        return self.graphs[pipeline_id]

    def summary(self) -> dict[str, Any]:
        return {
            "intents": len(self.intents),
            "goals": len(self.goals),
            "steps": len(self.steps),
            "graphs": len(self.graphs),
            "reasoning": len(self.reasoning),
            "reflections": len(self.reflections),
            "memory": len(self.memory),
            "world": len(self.world),
            "governance": len(self.governance),
            "learning": len(self.learning),
        }
