"""BeliefFormation — facade for the cognitive pipeline per-actor tick.

Three explicit responsibilities:

Engine Management
    __init__(engine)      — accept injected engine or create default
    _get_engine()         — lazy-init ComparisonIntegratedPolicy

Lifecycle Coordination
    from_state(state)     — run one complete cognitive cycle
    _build_result(state)  — extract FormationResult from CognitiveState

Result Reconciliation
    FormationResult       — outcome, losses, timing, metadata

Architecture position:
    SocietyRuntime.tick_one_actor()
        → CognitiveActor.tick()
            → BeliefFormation.from_state(state)     ← THIS CLASS
                → CognitivePolicy.execute(state)
                    → 10 pipeline stages

BeliefFormation is a thin facade — it does NOT implement any stage.
It owns: state construction, engine wiring, result reconciliation.
CognitiveRuntime owns: the stage implementations.
CognitivePolicy owns: the stage ordering.

The 10-stage pipeline:
    Observe → Believe → Plan → Predict → Execute → ObserveOutcome
    → Compare → Learn → CompileΦ → Commit

Predict (a genuine blind forecast, simulated from the pre-execution
world_snapshot/belief) runs BEFORE Execute — see
pipeline/comparison/integration.py's module docstring for why running it
after Execute (an earlier version of this pipeline did) made the
prediction-vs-outcome comparison meaningless.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState

logger = logging.getLogger("agentos.pipeline.belief_formation")


@dataclass
class FormationResult:
    """Result of one complete belief formation cycle."""

    success: bool = True
    goal_achieved: bool = False
    reward: float = 0.0
    comparison_score: float = 0.0
    actor_loss: float = 0.0
    world_loss: float = 0.0
    policy_loss: float = 0.0
    actions_executed: int = 0
    duration_ms: float = 0.0
    stage_durations: dict[str, float] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BeliefFormation:
    """Facade for the cognitive pipeline per-actor tick.

    Coordinates the full cognitive lifecycle without implementing any stage.
    """

    def __init__(self, engine: Any = None) -> None:
        """
        Args:
            engine: A CognitiveRuntime instance.
                    If None, lazily creates a ComparisonIntegratedPolicy runtime.
        """
        self._engine = engine

    # ──────────────────────────────────────────────────────────
    # Engine Management
    # ──────────────────────────────────────────────────────────

    def _get_engine(self) -> Any:
        """Lazy-init the cognitive engine."""
        if self._engine is None:
            from src.monkey_brain.kernel.pipeline.comparison.integration import (
                build_comparison_integrated_runtime,
            )

            self._engine = build_comparison_integrated_runtime()
        return self._engine

    # ──────────────────────────────────────────────────────────
    # Lifecycle Coordination
    # ──────────────────────────────────────────────────────────

    async def from_state(
        self,
        state: CognitiveState,
        *,
        timeout_seconds: float = 60.0,
    ) -> FormationResult:
        """Run one complete belief formation cycle.

        Single entry point for actor cognitive ticks.
        Builds the result from the completed CognitiveState.

        Args:
            state: The actor's CognitiveState (belief persists across cycles).
            timeout_seconds: Max time for the full cycle.

        Returns:
            FormationResult with outcome, losses, and timing.
        """
        start = time.time()

        try:
            import asyncio

            engine = self._get_engine()

            from src.monkey_brain.kernel.pipeline.tuning import get_tuning

            tuning = get_tuning()
            if hasattr(engine, "_policy"):
                tuning.apply_to_policy(engine._policy)
            if hasattr(engine, "_execution_engine"):
                tuning.apply_to_executor(engine._execution_engine)

            state = await asyncio.wait_for(
                engine.tick(state),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Belief formation timed out after %ds", timeout_seconds)
            return FormationResult(
                success=False,
                duration_ms=(time.time() - start) * 1000,
                errors=[
                    {
                        "type": "TimeoutError",
                        "message": f"Timed out after {timeout_seconds}s",
                    }
                ],
            )
        except Exception as e:
            logger.error("Belief formation failed: %s", e)
            return FormationResult(
                success=False,
                duration_ms=(time.time() - start) * 1000,
                errors=[{"type": type(e).__name__, "message": str(e)[:500]}],
            )

        duration_ms = (time.time() - start) * 1000

        from src.monkey_brain.kernel.pipeline.tuning import get_tuning

        state.pruning_result = get_tuning().decay_and_prune(state.belief)

        return self._build_result(state, duration_ms)

    # ──────────────────────────────────────────────────────────
    # Result Reconciliation
    # ──────────────────────────────────────────────────────────

    def _build_result(self, state: CognitiveState, duration_ms: float) -> FormationResult:
        """Extract FormationResult from the completed CognitiveState."""
        outcome = state.outcome or {}
        goal_achieved = bool(outcome.get("goal_achieved", False))

        learning = getattr(state, "learning", None) or {}
        comparison = getattr(state, "comparison_result", None) or {}
        pruning = getattr(state, "pruning_result", None) or {}

        actions_executed = len(state.actions)
        exec_result = state.execution_result or {}
        if isinstance(exec_result, dict):
            actions_executed = actions_executed or exec_result.get("action_count", 0)

        return FormationResult(
            success=not bool(state.errors),
            goal_achieved=goal_achieved,
            reward=learning.get("reward", 0.0),
            comparison_score=comparison.get("comparison_score", 0.0),
            actor_loss=comparison.get("actor_loss", 0.0),
            world_loss=comparison.get("world_loss", 0.0),
            policy_loss=comparison.get("policy_loss", 0.0),
            actions_executed=actions_executed,
            duration_ms=duration_ms,
            stage_durations=state.stage_durations,
            errors=state.errors,
            metadata={
                "cycle_count": state.actor.cycle_count if state.actor else 0,
                "belief_facts": len(state.belief.facts) if state.belief else 0,
                "hypotheses": len(state.belief.hypotheses) if state.belief else 0,
                "phi": state.phi is not None,
                "learning_rationale": learning.get("learning_rationale", ""),
                "pruned_facts": pruning.get("pruned_facts", 0),
                "pruned_hypotheses": pruning.get("pruned_hypotheses", 0),
            },
        )
