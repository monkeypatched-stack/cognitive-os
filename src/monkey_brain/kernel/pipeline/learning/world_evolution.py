"""World Model Evolution (Step 10.5) — reinforces or decays *shared* world
relationship strengths, as distinct from Step 10.4's per-actor belief
confidence.

The distinction is architectural, not cosmetic (see [[project_world_policy_split]]
in project memory: the world model P(S'|S) is shared/global, while an actor's
belief Q(S,A) is local and subjective). Step 10.4 already updates the
*actor's own* confidence in an observation immediately and relatively
aggressively (learning_rate=0.3) — that's fine, it only affects the one
actor. This step updates the *shared* world model that every actor eventually
reads from, so it moves far more conservatively (learning_rate=0.05 by
default): one actor's one experience shouldn't swing a relationship every
other actor relies on.

Like belief_learning.py, this only computes what the update *would be* — it
has no access to and does not modify the real world model runtime. It reads
prior relationship strength from an optional `current_strengths` lookup
(defaulting to a neutral 0.5 "unknown" prior when absent), the same
inject-the-current-state-as-a-parameter pattern Step 9.5's CapabilityResolver
used for ExecutionEnvironment — Step 10.7 is what will eventually supply
`current_strengths` from a real persistent store.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningObservation,
    LearningSignal,
)

NEUTRAL_STRENGTH = 0.5


@dataclass(frozen=True)
class WorldRelationshipUpdate:
    """A proposed strength revision for one shared world relationship,
    identified the same way LearningObservation identifies a fact:
    (entity, attribute)."""

    entity: str = ""
    attribute: str = ""
    previous_strength: float = NEUTRAL_STRENGTH
    new_strength: float = NEUTRAL_STRENGTH
    delta: float = 0.0
    rationale: str = ""


def _signed_reward(reward: float) -> float:
    return max(-1.0, min(1.0, 2.0 * reward - 1.0))


def derive_world_updates(
    observations: tuple[LearningObservation, ...],
    reward: float,
    current_strengths: dict[tuple[str, str], float] | None = None,
    learning_rate: float = 0.05,
) -> tuple[WorldRelationshipUpdate, ...]:
    """Pure function, same reward-weighted headroom-scaled rule as Step
    10.4's derive_belief_updates(), but starting from a shared prior
    (current_strengths, defaulting to NEUTRAL_STRENGTH) rather than the
    observation's own per-actor confidence, and moving at a much slower
    default rate."""
    signed = _signed_reward(reward)
    if signed == 0.0:
        return ()

    strengths = current_strengths or {}
    target = 1.0 if signed > 0 else 0.0
    updates = []
    for obs in observations:
        key = (obs.entity, obs.attribute)
        prior = strengths.get(key, NEUTRAL_STRENGTH)
        delta = learning_rate * abs(signed) * (target - prior)
        if delta == 0.0:
            continue
        new_strength = max(0.0, min(1.0, prior + delta))
        actual_delta = new_strength - prior
        if actual_delta == 0.0:
            continue
        direction = "reinforced" if actual_delta > 0 else "weakened"
        updates.append(
            WorldRelationshipUpdate(
                entity=obs.entity,
                attribute=obs.attribute,
                previous_strength=prior,
                new_strength=new_strength,
                delta=actual_delta,
                rationale=f"{obs.entity} {obs.attribute.replace('_', ' ')} relationship {direction} (reward {reward:.2f})",
            )
        )
    return tuple(updates)


def _updates_to_signals(
    updates: tuple[WorldRelationshipUpdate, ...],
) -> tuple[LearningSignal, ...]:
    return tuple(
        LearningSignal(
            kind="world",
            subject=f"{u.entity}.{u.attribute}",
            direction=max(-1.0, min(1.0, u.delta * 10.0)),
            strength=min(1.0, abs(u.delta)),
            metadata={"rationale": u.rationale, "new_strength": u.new_strength},
        )
        for u in updates
    )


class WorldEvolutionEngine:
    """Derives world-relationship updates and "world" signals from a
    rewarded experience's observations, folding the signals back into the
    experience (frozen -> returns a new instance)."""

    def __init__(
        self,
        learning_rate: float = 0.05,
        current_strengths: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._learning_rate = learning_rate
        self._current_strengths = current_strengths

    def derive_updates(self, experience: LearningExperience) -> tuple[WorldRelationshipUpdate, ...]:
        return derive_world_updates(
            experience.observations,
            experience.reward,
            self._current_strengths,
            self._learning_rate,
        )

    def learn(self, experience: LearningExperience) -> LearningExperience:
        updates = self.derive_updates(experience)
        signals = _updates_to_signals(updates)
        metadata = dict(experience.metadata)
        metadata["world_updates"] = [dataclasses.asdict(u) for u in updates]
        return dataclasses.replace(experience, signals=experience.signals + signals, metadata=metadata)


def apply_world_evolution(experience: LearningExperience, **kwargs) -> LearningExperience:
    """Module-level convenience wrapper around WorldEvolutionEngine().learn()."""
    return WorldEvolutionEngine(**kwargs).learn(experience)
