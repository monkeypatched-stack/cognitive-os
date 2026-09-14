"""Φ (Phi) Compiler (Step 10.8) — compresses a LearningExperience +
LearningResult into a structured, persistable summary: "Φ Artifact:
Structured summary of the cognitive cycle, ready for persistence, sharing,
or future prediction" (Step 10's own acceptance criteria).

Depends only on learning.domain, same as reward.py/belief_learning.py/
world_evolution.py — no CognitiveState coupling here either. PhiCompiler
takes the experience captured by 10.2 and the result produced by 10.6's
policy, and produces one flat, fully-JSON-safe PhiArtifact (no Any-typed
fields, unlike LearningExperience — by the time you're compiling a
persistable summary, the algorithm-independent goal/plan/execution objects
have already served their purpose upstream).

Reuses capture.py's own metadata convention rather than inventing a new
one: ExperienceBuilder already writes `metadata["goal_name"]` on every
captured experience (Step 10.2), so `goal_signature` reads it back instead
of re-deriving a goal key from scratch — keeping the two steps' contracts
consistent.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningResult,
    Provenance,
    experience_tenant_id,
    scoped_goal_signature,
)


@dataclass(frozen=True)
class PhiArtifact:
    """A compact, fully-serializable summary of one cognitive cycle."""

    phi_id: str = field(default_factory=lambda: uuid4().hex)
    experience_id: str = ""
    goal_signature: str = ""
    outcome_summary: str = ""
    reward: float = 0.0
    confidence: float = 0.0
    belief_updated: bool = False
    world_updated: bool = False
    signal_count: int = 0
    top_signal_summary: str = ""
    provenance: Provenance = field(default_factory=Provenance)
    compiled_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


def _goal_signature(experience: LearningExperience) -> str:
    signature = experience.metadata.get("goal_name")
    if not signature:
        goal = experience.goal
        signature = str(getattr(goal, "name", None) or getattr(goal, "description", None) or goal or "unknown_goal")
    return scoped_goal_signature(str(signature), experience_tenant_id(experience))


def _outcome_summary(experience: LearningExperience, reward: float) -> str:
    outcome = experience.outcome
    goal_desc = _goal_signature(experience)
    if outcome.goal_achieved:
        return f"Achieved '{goal_desc}' in {outcome.duration_seconds:.0f}s (reward {reward:.2f})"
    if outcome.partial:
        return f"Partially achieved '{goal_desc}' (reward {reward:.2f})"
    return f"Failed to achieve '{goal_desc}' (reward {reward:.2f})"


def _top_signal_summary(result: LearningResult) -> str:
    if not result.signals_applied:
        return ""
    top = max(result.signals_applied, key=lambda s: s.strength)
    direction = "reinforces" if top.direction > 0 else "weakens" if top.direction < 0 else "is neutral on"
    return f"{top.kind} signal {direction} {top.subject} (strength {top.strength:.2f})"


class PhiCompiler:
    """Compiles a captured LearningExperience + the LearningResult a
    LearningPolicyEngine produced from it into one persistable PhiArtifact."""

    def compile(self, experience: LearningExperience, result: LearningResult) -> PhiArtifact:
        return PhiArtifact(
            experience_id=result.experience_id or experience.experience_id,
            goal_signature=_goal_signature(experience),
            outcome_summary=_outcome_summary(experience, result.reward),
            reward=result.reward,
            confidence=experience.confidence,
            belief_updated=result.belief_updated,
            world_updated=result.world_updated,
            signal_count=len(result.signals_applied),
            top_signal_summary=_top_signal_summary(result),
            provenance=experience.provenance,
            metadata=dict(experience.metadata),
        )


def compile_phi(experience: LearningExperience, result: LearningResult) -> PhiArtifact:
    """Module-level convenience wrapper around PhiCompiler().compile()."""
    return PhiCompiler().compile(experience, result)


def phi_to_dict(artifact: PhiArtifact) -> dict[str, Any]:
    """JSON-safe serialization. Unlike experience_to_dict() (capture.py),
    PhiArtifact has no Any-typed fields, so a plain asdict() is sufficient
    -- no _safe_serialize fallback machinery needed."""
    return dataclasses.asdict(artifact)
