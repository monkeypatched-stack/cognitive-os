"""Belief Learning (Step 10.4) — turns a rewarded LearningExperience's
observations into belief-confidence updates and "belief" LearningSignals.

Pipeline order (per Step 10's acceptance criteria): Experience Capture (10.2)
-> Reward Evaluation (10.3) -> Belief Update (this) -> World Model Update
(10.5). BeliefLearner therefore expects `experience.reward` to already be
populated by RewardEngine.evaluate() — reward is the evidence strength this
step uses to decide whether an observation should be trusted more or less.

Like reward.py, this is deliberately self-contained: it depends only on
learning.domain, not on the real belief_state.BeliefState or
CognitiveRuntime. It computes what the belief update *would be*; actually
applying it to the live BeliefState is Step 10.7's integration job, the same
split Step 9's ExecutionScheduler/RetryExecutor (compute the plan) vs. 9.7's
IntegratedExecutionEngine (wire it into the real runtime) already used.

BeliefUpdate lives here rather than in domain.py, matching the precedent
RewardWeights/RewardBreakdown set in Step 10.3 (and RecoveryAction/
RecoveryPolicy in Step 9.4): sub-step-specific behavioral types stay with
their engine.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningObservation,
    LearningSignal,
)


@dataclass(frozen=True)
class BeliefUpdate:
    """A proposed confidence revision for one observed (entity, attribute)."""

    entity: str = ""
    attribute: str = ""
    previous_confidence: float = 0.0
    new_confidence: float = 0.0
    delta: float = 0.0
    rationale: str = ""


def _signed_reward(reward: float) -> float:
    """Maps reward in [0, 1] to [-1, 1] around a neutral midpoint of 0.5 —
    an average outcome moves nothing; success reinforces, failure corrects."""
    return max(-1.0, min(1.0, 2.0 * reward - 1.0))


def derive_belief_updates(
    observations: tuple[LearningObservation, ...],
    reward: float,
    learning_rate: float = 0.3,
) -> tuple[BeliefUpdate, ...]:
    """Pure function: for each observation, nudge its confidence toward 1.0
    (reward > neutral) or 0.0 (reward < neutral), scaled by how strong the
    reward signal was and how much headroom the current confidence has left.
    A neutral reward (0.5) produces no update at all — no evidence, no change.
    """
    signed = _signed_reward(reward)
    if signed == 0.0:
        return ()

    target = 1.0 if signed > 0 else 0.0
    updates = []
    for obs in observations:
        delta = learning_rate * abs(signed) * (target - obs.confidence)
        if delta == 0.0:
            continue
        new_confidence = max(0.0, min(1.0, obs.confidence + delta))
        actual_delta = new_confidence - obs.confidence
        if actual_delta == 0.0:
            continue
        direction = "reinforces" if actual_delta > 0 else "weakens"
        updates.append(
            BeliefUpdate(
                entity=obs.entity,
                attribute=obs.attribute,
                previous_confidence=obs.confidence,
                new_confidence=new_confidence,
                delta=actual_delta,
                rationale=f"reward {reward:.2f} {direction} observation ({obs.entity}.{obs.attribute})",
            )
        )
    return tuple(updates)


def _updates_to_signals(
    updates: tuple[BeliefUpdate, ...],
) -> tuple[LearningSignal, ...]:
    return tuple(
        LearningSignal(
            kind="belief",
            subject=f"{u.entity}.{u.attribute}",
            direction=max(-1.0, min(1.0, u.delta * 10.0)),
            strength=min(1.0, abs(u.delta)),
            metadata={"rationale": u.rationale, "new_confidence": u.new_confidence},
        )
        for u in updates
    )


class BeliefLearner:
    """Derives belief updates and signals from a rewarded experience's
    observations, and folds the resulting signals back into the
    experience (frozen -> returns a new instance, same idiom RewardEngine
    uses)."""

    def __init__(self, learning_rate: float = 0.3) -> None:
        self._learning_rate = learning_rate

    def derive_updates(self, experience: LearningExperience) -> tuple[BeliefUpdate, ...]:
        return derive_belief_updates(experience.observations, experience.reward, self._learning_rate)

    def learn(self, experience: LearningExperience) -> LearningExperience:
        updates = self.derive_updates(experience)
        signals = _updates_to_signals(updates)
        metadata = dict(experience.metadata)
        metadata["belief_updates"] = [dataclasses.asdict(u) for u in updates]
        return dataclasses.replace(experience, signals=experience.signals + signals, metadata=metadata)


def apply_belief_learning(experience: LearningExperience, **kwargs) -> LearningExperience:
    """Module-level convenience wrapper around BeliefLearner().learn()."""
    return BeliefLearner(**kwargs).learn(experience)
