"""Experience capture (Step 10.2) — every cognitive cycle produces a
reusable experience.

ExperienceBuilder.capture() turns a completed cognitive cycle's
CognitiveState (the type that flows through every cycle in
belief_runtime.py's Observe -> Commit lifecycle, regardless of which
PlanningEngine/ExecutionEngine happen to be injected) into an immutable
LearningExperience:

    Beliefs -> Goal -> Plan -> Execution -> Outcome -> Experience

Pure transcription — no reward computation, no belief/world updates, no
learning-policy decisions. Those are Steps 10.3-10.6. This module only
captures what already happened.

CognitiveState is imported (for type-hinting only, not modified) from
execution_state.py — reading the shape of a protected file's type is not
the same as changing it, the same distinction Steps 8.7/9.7 relied on when
importing belief_state.BeliefState / execution.py's Action to build their
own conversion layers.

capture() reads state at the point belief_runtime.py's own lifecycle calls
it: after _observe_outcome (state.outcome populated) but conceptually
alongside _learn — before _compile_phi/_commit run. Step 10.7 is what
actually wires capture() into that stage; this module works standing alone,
the same way every Step 8/9 building-block module did before its own
integration step.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningEvent,
    LearningExperience,
    LearningObservation,
    LearningOutcome,
    Provenance,
)


class ExperienceBuilder:
    """Captures a LearningExperience from a completed cognitive cycle's
    CognitiveState. No learning algorithm — pure extraction."""

    def __init__(self, default_source: str = "cognitive_cycle") -> None:
        self._default_source = default_source

    def capture(self, state: CognitiveState) -> LearningExperience:
        return LearningExperience(
            goal=state.belief.goal,
            plan=state.belief.plan,
            execution=state.execution_result,
            observations=self._extract_observations(state),
            outcome=self._extract_outcome(state),
            # Unset — Step 10.3 (Reward & Evaluation Engine) computes this.
            # 0.0 is the same "not yet judged" default used throughout this
            # model (e.g. PlanCandidate.score stayed unset through Steps
            # 8.5-8.6 the same way).
            reward=0.0,
            confidence=state.belief.confidence(),
            timestamp=state.created_at,
            provenance=self._extract_provenance(state),
            events=self._extract_events(state),
            signals=(),  # Steps 10.3-10.5's job to derive
            metadata={
                "intent_type": state.belief.intent.type,
                "goal_name": state.belief.goal.name,
                "actor_id": state.actor_id,
            },
        )

    # ── Beliefs -> Observations ──────────────────────────────────────────

    def _extract_observations(self, state: CognitiveState) -> tuple[LearningObservation, ...]:
        return tuple(
            LearningObservation(
                entity=fact.entity,
                attribute=fact.attribute,
                value=fact.value,
                confidence=fact.confidence,
                source=fact.source,
                observed_at=fact.observed_at,
            )
            for fact in state.belief.facts
        )

    # ── Execution -> Outcome ─────────────────────────────────────────────

    def _extract_outcome(self, state: CognitiveState) -> LearningOutcome:
        outcome_dict = state.outcome or {}
        goal_achieved = bool(outcome_dict.get("goal_achieved", False))
        success_count = outcome_dict.get("success_count", 0)
        partial = (not goal_achieved) and success_count > 0
        duration_seconds = outcome_dict.get("total_latency_ms", 0.0) / 1000.0
        errors = tuple(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in state.errors)
        return LearningOutcome(
            goal_achieved=goal_achieved,
            partial=partial,
            cost=0.0,
            duration_seconds=duration_seconds,
            errors=errors,
            metadata=dict(outcome_dict),
        )

    # ── Provenance ───────────────────────────────────────────────────────

    def _extract_provenance(self, state: CognitiveState) -> Provenance:
        run_id = ""
        compiled = getattr(state, "compiled", None)
        execution_context = getattr(compiled, "execution_context", None)
        if execution_context is not None:
            run_id = getattr(execution_context, "run_id", "") or ""

        return Provenance(
            actor_id=state.actor_id,
            tenant_id=state.tenant_id,
            run_id=run_id,
            source=self._default_source,
        )

    # ── Execution trace -> Events ────────────────────────────────────────

    def _extract_events(self, state: CognitiveState) -> tuple[LearningEvent, ...]:
        return tuple(
            LearningEvent(
                event_type=f"{entry.stage}:{entry.action}",
                description=entry.detail,
                timestamp=entry.timestamp,
            )
            for entry in state.execution_trace
        )


def capture_experience(state: CognitiveState) -> LearningExperience:
    """Module-level convenience wrapper around ExperienceBuilder().capture()."""
    return ExperienceBuilder().capture(state)


# ── Serialization ────────────────────────────────────────────────────────


def experience_to_dict(experience: LearningExperience) -> dict[str, Any]:
    """JSON-safe serialization. goal/plan/execution are Any-typed (see
    domain.py) and may be dataclasses, dicts, or arbitrary objects —
    dataclasses serialize via asdict(), everything else falls back to
    str(), the same graceful-degradation CognitiveState.to_dict() itself
    already uses for its own plan/phi fields.
    """
    return {
        "experience_id": experience.experience_id,
        "goal": _safe_serialize(experience.goal),
        "plan": _safe_serialize(experience.plan),
        "execution": _safe_serialize(experience.execution),
        "observations": [asdict(o) for o in experience.observations],
        "outcome": asdict(experience.outcome),
        "reward": experience.reward,
        "confidence": experience.confidence,
        "timestamp": experience.timestamp,
        "provenance": asdict(experience.provenance),
        "events": [asdict(e) for e in experience.events],
        "signals": [asdict(s) for s in experience.signals],
        "metadata": _safe_serialize(experience.metadata),
    }


def _safe_serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _safe_serialize(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _safe_serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_serialize(v) for v in value]
    return str(value)
