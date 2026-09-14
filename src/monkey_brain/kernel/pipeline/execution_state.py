"""CognitiveState — the canonical state object for one execution cycle.

Represents a single actor's complete cognitive state during one
Observe → Commit cycle.

CognitiveState owns transient reasoning data.
BeliefState owns cognitive knowledge.

Relationship:
    CognitiveRuntime
        → CognitiveState (transient, mutable, discarded after cycle)
            → BeliefState (actor knowledge model)

Lifecycle:
    Created at the start of CognitiveRuntime.run().
    Enriched by each cognitive stage.
    Consumed at the end to produce ExecutionResult.
    Discarded when execution completes.

Not persisted. Not shared between requests. Not global state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.cognitive_delta import CognitiveDelta
from src.monkey_brain.kernel.pipeline.observations import ObservationSet
from src.monkey_brain.kernel.pipeline.execution import ActionOutcome, ExecutionResult


@dataclass(frozen=True)
class WorldSnapshot:
    """Frozen snapshot of the world at the start of a reasoning cycle.

    Ensures every stage in the lifecycle operates on a consistent world view.
    Created once during Observe, never mutated, referenced by all subsequent stages.

    Without this, a world mutation between Observe and Plan would cause
    the plan to be based on a different world than the observations.
    """

    states: tuple[str, ...] = ()
    """All world states at snapshot time."""
    state_count: int = 0
    """Number of states."""
    domain_count: int = 0
    """Number of domains."""
    transition_count: int = 0
    """Number of transitions (nnz)."""
    timestamp: float = field(default_factory=time.time)
    """When the snapshot was taken."""


@dataclass
class CognitiveState:
    """The canonical execution object flowing through the cognitive lifecycle.

    Every stage in CognitiveRuntime receives and returns the same instance.
    No stage replaces it — each stage enriches it.

    Ownership:
        CognitiveState owns transient execution data.
        BeliefState owns cognitive knowledge.
        Stages update BeliefState through CognitiveState, never bypassing it.
    """

    # ── Input (set once, never mutated by stages) ─────────────────────────

    compiled: Any = None
    """The fully compiled request. Immutable reference."""

    context: Any = None
    """RuntimeContext with service handles."""

    # ── Actor (the entity that reasons) ───────────────────────────────────

    actor: Actor | None = None
    """The actor performing this reasoning cycle.
    Identity only — does NOT own BeliefState."""

    # ── Identity (convenience accessors from actor) ───────────────────────

    @property
    def actor_id(self) -> str:
        return self.actor.actor_id if self.actor else ""

    @property
    def tenant_id(self) -> str:
        return self.actor.tenant_id if self.actor else "default"

    # ── World View (read-only reference) ──────────────────────────────────

    world_view: Any = None
    """Reference to the actor's view of the world. Never mutated."""

    # ── World Snapshot (frozen at Observe, referenced by all stages) ──────

    world_snapshot: WorldSnapshot | None = None
    """Frozen snapshot of the world taken at the start of Observe.
    All subsequent stages MUST reason over this snapshot, not the live world."""

    # ── Pending Observations (populated by _observe, consumed by _update_beliefs) ──

    pending_observations: ObservationSet | None = None
    """Observations from the provider, waiting to be fused into belief.
    Set by _observe, consumed by _update_beliefs via BeliefFusion."""

    # ── Belief (owned by CognitiveRuntime, composed here per-cycle) ───────

    belief: BeliefState = field(default_factory=BeliefState)
    """The belief state for this reasoning cycle.
    Created by the runtime from BeliefStore. Not owned by Actor."""

    # ── Goal & Intent (convenience accessors into BeliefState) ────────────

    @property
    def goal(self) -> Any:
        """Current goal (delegates to BeliefState.goal)."""
        return self.belief.goal

    @goal.setter
    def goal(self, value: Any) -> None:
        self.belief.goal = value

    @property
    def intent(self) -> Any:
        """Current intent (delegates to BeliefState.intent)."""
        return self.belief.intent

    @intent.setter
    def intent(self, value: Any) -> None:
        self.belief.intent = value

    # ── Plan (populated by _generate_plan) ────────────────────────────────

    plan: Any = None
    """Current execution plan."""

    # ── Compiled Plan Graph (populated by _execute_plan, before dispatch) ──

    compiled_plan_graph: Any = None
    """kernel.pipeline.plan_compiler.CompiledPlanGraph -- the validated,
    provenance-carrying artifact _execute_plan compiles the selected plan
    into before any capability is invoked. None when the plan was rejected
    at compile (see _reject_plan) or execution never reached this stage.
    Observability/Timeline-reconstruction only -- never read by
    ActionExecutor's dispatch loop or any downstream stage's control flow."""

    compiled_execution_graph: Any = None
    """kernel.execute.graph.ExecutionGraph -- the canonical executable DAG
    produced alongside compiled_plan_graph. Same rejection/observability
    contract as compiled_plan_graph; ActionExecutor still dispatches the
    flat plan.steps sequence today."""

    # ── Actions (populated by _execute_plan) ──────────────────────────────

    actions: list[ActionOutcome] = field(default_factory=list)
    """Outcomes for each action executed during this cycle."""

    # ── Execution Result (populated by _execute_plan) ─────────────────────

    execution_result: ExecutionResult | None = None
    """Aggregated result of all actions in the plan."""

    # ── Outcome (populated by _observe_outcome) ───────────────────────────

    outcome: dict[str, Any] = field(default_factory=dict)
    """Observed execution outcome: success/failure, metrics."""

    # ── Phi (populated by _compile_phi) ───────────────────────────────────

    phi: Any = None
    """Compiled action operator (policy-weighted transitions)."""

    # ── Prediction (populated by _predict) ────────────────────────────────

    prediction_result: dict[str, Any] | None = None
    """Prediction outcome from the Predict stage (counterfactuals, scenarios)."""

    # ── Comparison (populated by compare stage) ───────────────────────────

    comparison_result: dict[str, Any] | None = None
    """Comparison of prediction vs execution outcome (losses, diffs)."""

    # ── Pruning (populated after tick) ────────────────────────────────────

    pruning_result: dict[str, int] | None = None
    """Pruning stats: facts and hypotheses removed during decay."""

    # ── Transition Model (learned across ticks) ──────────────────────────

    transition_model: Any = None
    """Learned transition model from comparison results. Persists across
    ticks via the CognitiveState reference, feeding back into prediction."""

    # ── Learning (populated by _learn) ────────────────────────────────────

    learning: dict[str, Any] | None = None
    """Learning result: experience_id, reward, belief_updated, etc."""

    # ── Execution Result (populated by _commit) ───────────────────────────

    execution_result: dict[str, Any] | None = None
    """Final execution result produced by the lifecycle."""

    # ── Cognitive Delta (emitted by _commit) ──────────────────────────────

    cognitive_delta: CognitiveDelta | None = None
    """Structured artifact produced by Commit.
    Captures everything that changed during this reasoning cycle.
    Consumed by infrastructure for checkpoint, world update, replication."""

    # ── Diagnostics (accumulated across stages) ───────────────────────────

    diagnostics: list[Diagnostic] = field(default_factory=list)
    """Execution diagnostics: warnings, recoverable errors, reasoning metadata."""

    # ── Execution Trace (accumulated across stages) ───────────────────────

    execution_trace: list[TraceEntry] = field(default_factory=list)
    """Ordered trace of execution steps for debugging and observability."""

    # ── Metrics (accumulated across stages) ───────────────────────────────

    metrics: dict[str, Any] = field(default_factory=dict)
    """Quantitative metrics: timing, counts, scores."""

    # ── Errors (captured per-stage) ───────────────────────────────────────

    errors: list[dict[str, Any]] = field(default_factory=list)
    """Fatal errors captured during lifecycle stages."""

    # ── Timestamps ────────────────────────────────────────────────────────

    created_at: float = field(default_factory=time.time)
    """When this state was created."""

    stage_durations: dict[str, float] = field(default_factory=dict)
    """Per-stage wall-clock time in milliseconds."""

    # ══════════════════════════════════════════════════════════════════════
    # Recording API
    # ══════════════════════════════════════════════════════════════════════

    def record_stage(self, stage: str, duration_ms: float) -> None:
        """Record the duration of a cognitive stage."""
        self.stage_durations[stage] = round(duration_ms, 2)

    def record_error(self, stage: str, error: Exception) -> None:
        """Capture a fatal error from a cognitive stage."""
        self.errors.append(
            {
                "stage": stage,
                "type": type(error).__name__,
                "message": str(error)[:500],
            }
        )

    def record_warning(self, stage: str, message: str) -> None:
        """Record a recoverable warning (non-fatal)."""
        self.diagnostics.append(
            Diagnostic(
                level="warning",
                stage=stage,
                message=message,
            )
        )

    def record_diagnostic(self, stage: str, key: str, value: Any) -> None:
        """Record reasoning metadata for observability."""
        self.diagnostics.append(
            Diagnostic(
                level="info",
                stage=stage,
                message=f"{key}={value}",
                metadata={"key": key, "value": value},
            )
        )

    def add_trace(self, stage: str, action: str, detail: str = "") -> None:
        """Add an entry to the execution trace."""
        self.execution_trace.append(
            TraceEntry(
                stage=stage,
                action=action,
                detail=detail,
                timestamp=time.time(),
            )
        )

    # ══════════════════════════════════════════════════════════════════════
    # Query API
    # ══════════════════════════════════════════════════════════════════════

    def has_errors(self) -> bool:
        """Check if any stage recorded fatal errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if any stage recorded warnings."""
        return any(d.level == "warning" for d in self.diagnostics)

    def warnings(self) -> list[Diagnostic]:
        """Get all warning-level diagnostics."""
        return [d for d in self.diagnostics if d.level == "warning"]

    # ══════════════════════════════════════════════════════════════════════
    # Serialization
    # ══════════════════════════════════════════════════════════════════════

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "belief": self.belief.to_dict(),
            "plan": str(self.plan) if self.plan else None,
            "actions_count": len(self.actions),
            "outcome": self.outcome,
            "phi": str(self.phi) if self.phi else None,
            "diagnostics": len(self.diagnostics),
            "execution_trace": len(self.execution_trace),
            "metrics": self.metrics,
            "errors": self.errors,
            "created_at": self.created_at,
            "stage_durations": self.stage_durations,
        }

    def summary(self) -> dict[str, Any]:
        """Produce a summary of the cognitive state."""
        return {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "belief": self.belief.summary(),
            "has_plan": self.plan is not None,
            "actions": len(self.actions),
            "has_phi": self.phi is not None,
            "diagnostics": len(self.diagnostics),
            "warnings": len(self.warnings()),
            "trace_entries": len(self.execution_trace),
            "errors": len(self.errors),
            "stage_durations": self.stage_durations,
        }


# ════════════════════════════════════════════════════════════════════════════
# Diagnostic & Trace types
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class Diagnostic:
    """A diagnostic entry: warning, info, or recoverable error."""

    level: str = "info"
    """Severity: 'info', 'warning', 'error'."""
    stage: str = ""
    """Which cognitive stage produced this."""
    message: str = ""
    """Human-readable description."""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Structured metadata."""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TraceEntry:
    """A single step in the execution trace."""

    stage: str = ""
    """Which cognitive stage."""
    action: str = ""
    """What was done."""
    detail: str = ""
    """Additional detail."""
    timestamp: float = field(default_factory=time.time)
