"""Action Execution — plans become real actions through the Capability Bus.

Plan → Action → ExecutionEngine → CapabilityBus → Outcome

The runtime coordinates execution. It does not implement capabilities.
The ExecutionEngine discovers and invokes capabilities via the CapabilityBus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Any


@dataclass(frozen=True)
class Action:
    """A single executable action produced from a plan step.

    Actions are immutable — they describe WHAT to do, not the result.
    """

    action_id: str = ""
    """Unique identifier for this action."""
    capability: str = ""
    """Which capability to invoke (e.g. 'find_item', 'add_to_cart')."""
    parameters: dict[str, Any] = field(default_factory=dict)
    """Parameters for the capability."""
    preconditions: tuple[str, ...] = ()
    """What must be true before execution."""
    expected_outcome: str = ""
    """What this action is expected to achieve."""
    confidence: float = 0.0
    """Confidence in this action. [0.0, 1.0]"""
    source_step: str = ""
    """Which plan step this action came from."""
    correlation_id: str = ""
    """Id of the cognitive tick (execution_id) this action belongs to."""
    causation_id: str = ""
    """Id of the plan that produced this action (plan_id when available,
    else the tick's execution_id). Left empty rather than fabricated when
    neither is in scope at construction time."""
    step_index: int = -1
    """Absolute 0-based index of the originating step in plan.steps.
    Distinct from this Action's own position in the dispatched actions
    tuple, since a permission-denied step is filtered out before dispatch
    -- step_index is what lets ActionExecutor map a depends_on reference
    (below) back to the right prior action regardless of any such
    filtering. -1 (default) means unknown/not applicable, preserving every
    existing construction site's behavior unchanged."""
    depends_on: tuple[int, ...] = ()
    """Absolute plan.steps indices (mirrors PlanStep.depends_on exactly --
    see belief_state.py) that must have succeeded before this action may
    be invoked. Empty (the default, every plan today) means no dependency
    was declared and this action always executes -- a strict no-op for
    every existing plan."""


@dataclass(frozen=True)
class ActionOutcome:
    """The result of executing a single action.

    Immutable — captures what happened, not what should happen.
    """

    action_id: str = ""
    """Which action produced this outcome."""
    success: bool = False
    """Whether the action completed successfully."""
    result: Any = None
    """The raw result from the capability."""
    error: str = ""
    """Error message if the action failed."""
    latency_ms: float = 0.0
    """How long the action took."""
    side_effects: tuple[str, ...] = ()
    """What changed in the world as a result."""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional outcome metadata."""


@dataclass(frozen=True)
class ExecutionResult:
    """Aggregated result of executing all actions in a plan."""

    actions: tuple[ActionOutcome, ...] = ()
    """Outcomes for each action executed."""
    success_count: int = 0
    """Number of successful actions."""
    failure_count: int = 0
    """Number of failed actions."""
    total_latency_ms: float = 0.0
    """Total execution time."""
    goal_achieved: bool = False
    """Whether the overall goal was achieved."""
    event_publish_ms: float = 0.0
    """Performance analysis instrumentation only (measurement, not a
    behavior change): time spent inside ActionExecutor._publish_action_event
    across all actions in this batch — the "Perturbation Publication" stage
    kernel/society/integration.py's cycle-timing report separates out from
    the rest of Execute/Act."""
    status: str = "completed"
    """Generic execution state machine (Qualification Gap Closure, Phase
    3): "completed" (the default, every existing caller's exact prior
    behavior) or "waiting_for_human" — a capability signaled
    {"requires_approval": True, ...} and ActionExecutor.execute() stopped
    the tick here rather than continuing to remaining steps or reporting
    a false success/failure. Distinct from goal_achieved (which stays
    False for a waiting tick, same as any incomplete one) so a caller can
    tell "genuinely failed" apart from "paused, awaiting a real decision"
    without inspecting individual action outcomes."""

    @property
    def is_success(self) -> bool:
        return self.failure_count == 0 and self.goal_achieved

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / max(total, 1)


class ExecutionEngine(Protocol):
    """Protocol for executing actions through capabilities.

    The runtime depends only on this interface.
    Implementations discover and invoke capabilities via the CapabilityBus.
    """

    async def execute(
        self,
        actions: tuple[Action, ...],
        context: Any = None,
    ) -> ExecutionResult:
        """Execute a sequence of actions.

        Multi-Actor Execution Handoff: async so a capability CAN itself be
        a real `async def handle()` (e.g. AskActorCapability's real NATS
        point-to-point request/reply) without blocking the event loop —
        see ActionExecutor._execute_action's conditional await. Every
        existing sync capability is unaffected; ActionExecutor awaits its
        own synchronous dispatch either way.

        Args:
            actions: The actions to execute (from a validated plan)
            context: Optional runtime context

        Returns:
            ExecutionResult with outcomes for each action
        """
        ...
