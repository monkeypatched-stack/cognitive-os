"""Learning Policies (Step 10.6) — the behavioral contract Step 10.1's
domain-model docstring deferred.

Step 10.1 left a tension unresolved on purpose: the spec calls
`LearningPolicy` a domain **object**, but Step 10.6 wants
`LearningPolicy.learn(experience)` as a **behavioral** contract, and a
frozen dataclass can't hold that kind of method. This module resolves it
the same way Step 9's ExecutionHandler (Protocol) + ExecutionRegistry
(behavior) resolved the analogous tension for execution capabilities:

    LearningPolicy        (domain.py, data)   -- WHICH strategy, with WHAT parameters
    LearningPolicyEngine  (this module, Protocol) -- HOW that strategy actually learns
    resolve_policy()                          -- turns one into the other

`domain.LearningPolicy` keeps its name and data-only shape; it never grows a
`.learn()` method. `LearningPolicyEngine` is the new behavioral interface,
and `resolve_policy(policy: LearningPolicy) -> LearningPolicyEngine` is the
bridge — a factory, not a subclass relationship, so `LearningPolicy`
instances stay trivially serializable.

Three concrete strategies, chosen to make "pluggable policy" a real,
demonstrable difference rather than one hardcoded pipeline wearing three
names:

- `"reinforcement"` (the domain model's own default strategy) — runs the
  full Reward -> Belief -> World pipeline built in 10.3-10.5 every time.
- `"passive"` — records reward for the historical record but applies no
  belief/world updates at all (e.g. for an observer actor that shouldn't
  influence shared or personal beliefs).
- `"conservative"` — only applies belief/world updates when the reward is
  decisively far from neutral (configurable threshold); ambiguous outcomes
  near reward=0.5 are recorded but not acted on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningPolicy,
    LearningResult,
    LearningSignal,
)
from src.monkey_brain.kernel.pipeline.learning.reward import ExperienceRewardEngine
from src.monkey_brain.kernel.pipeline.learning.belief_learning import BeliefLearner
from src.monkey_brain.kernel.pipeline.learning.world_evolution import (
    WorldEvolutionEngine,
)


@runtime_checkable
class LearningPolicyEngine(Protocol):
    """The behavioral contract Step 10.1 deferred: given a captured
    experience, decide how (or whether) to learn from it."""

    def learn(self, experience: LearningExperience) -> LearningResult: ...


def _new_signals(before: LearningExperience, after: LearningExperience) -> tuple[LearningSignal, ...]:
    """Both BeliefLearner and WorldEvolutionEngine only ever append to
    .signals, so the signals this policy pass actually contributed are
    everything past the caller's original count."""
    return tuple(after.signals[len(before.signals) :])


@dataclass
class ReinforcementLearningPolicy:
    """Default strategy — always runs Reward -> Belief -> World."""

    reward_engine: ExperienceRewardEngine | None = None
    belief_learner: BeliefLearner | None = None
    world_engine: WorldEvolutionEngine | None = None

    def __post_init__(self) -> None:
        self.reward_engine = self.reward_engine or ExperienceRewardEngine()
        self.belief_learner = self.belief_learner or BeliefLearner()
        self.world_engine = self.world_engine or WorldEvolutionEngine()

    def learn(self, experience: LearningExperience) -> LearningResult:
        evaluated = self.reward_engine.evaluate(experience)
        belief_updates = self.belief_learner.derive_updates(evaluated)
        with_belief = self.belief_learner.learn(evaluated)
        world_updates = self.world_engine.derive_updates(with_belief)
        with_world = self.world_engine.learn(with_belief)

        return LearningResult(
            experience_id=experience.experience_id,
            reward=evaluated.reward,
            signals_applied=_new_signals(experience, with_world),
            belief_updated=bool(belief_updates),
            world_updated=bool(world_updates),
            rationale=(
                f"reward {evaluated.reward:.2f}; "
                f"{len(belief_updates)} belief update(s), {len(world_updates)} world update(s)"
            ),
            # with_world.metadata chains forward reward_breakdown (from
            # RewardEngine.evaluate) + belief_updates (from BeliefLearner.learn)
            # + world_updates (from WorldEvolutionEngine.learn) -- each stage
            # copies the prior stage's metadata before adding its own key, so
            # this is the full per-stage breakdown Step 10.9's trace consumes,
            # already computed, at no extra cost.
            metadata=with_world.metadata,
        )


@dataclass
class PassiveLearningPolicy:
    """Records reward for the historical record; applies no belief/world
    updates. For actors that should observe without influencing beliefs."""

    reward_engine: ExperienceRewardEngine | None = None

    def __post_init__(self) -> None:
        self.reward_engine = self.reward_engine or ExperienceRewardEngine()

    def learn(self, experience: LearningExperience) -> LearningResult:
        evaluated = self.reward_engine.evaluate(experience)
        return LearningResult(
            experience_id=experience.experience_id,
            reward=evaluated.reward,
            signals_applied=(),
            belief_updated=False,
            world_updated=False,
            rationale=f"passive policy: reward {evaluated.reward:.2f} recorded, no belief/world updates applied",
            metadata=evaluated.metadata,
        )


@dataclass
class ConservativeLearningPolicy:
    """Only applies belief/world updates when the reward is decisively far
    from neutral (0.5) — ambiguous outcomes are recorded but not acted on,
    to avoid overreacting to a single marginal experience."""

    reward_engine: ExperienceRewardEngine | None = None
    belief_learner: BeliefLearner | None = None
    world_engine: WorldEvolutionEngine | None = None
    confidence_threshold: float = 0.3

    def __post_init__(self) -> None:
        self.reward_engine = self.reward_engine or ExperienceRewardEngine()
        self.belief_learner = self.belief_learner or BeliefLearner()
        self.world_engine = self.world_engine or WorldEvolutionEngine()

    def learn(self, experience: LearningExperience) -> LearningResult:
        evaluated = self.reward_engine.evaluate(experience)
        decisive = abs(evaluated.reward - 0.5) >= self.confidence_threshold

        if not decisive:
            return LearningResult(
                experience_id=experience.experience_id,
                reward=evaluated.reward,
                signals_applied=(),
                belief_updated=False,
                world_updated=False,
                rationale=(
                    f"reward {evaluated.reward:.2f} too ambiguous to act on (threshold={self.confidence_threshold})"
                ),
                metadata=evaluated.metadata,
            )

        belief_updates = self.belief_learner.derive_updates(evaluated)
        with_belief = self.belief_learner.learn(evaluated)
        world_updates = self.world_engine.derive_updates(with_belief)
        with_world = self.world_engine.learn(with_belief)

        return LearningResult(
            experience_id=experience.experience_id,
            reward=evaluated.reward,
            signals_applied=_new_signals(experience, with_world),
            belief_updated=bool(belief_updates),
            world_updated=bool(world_updates),
            rationale=(
                f"reward {evaluated.reward:.2f} decisive (threshold={self.confidence_threshold}); "
                f"{len(belief_updates)} belief update(s), {len(world_updates)} world update(s)"
            ),
            metadata=with_world.metadata,
        )


_STRATEGIES: dict[str, type] = {
    "reinforcement": ReinforcementLearningPolicy,
    "passive": PassiveLearningPolicy,
    "conservative": ConservativeLearningPolicy,
}


def resolve_policy(policy: LearningPolicy = LearningPolicy()) -> LearningPolicyEngine:
    """Turns a data-only LearningPolicy (strategy name + parameters) into a
    concrete LearningPolicyEngine. Unknown strategy names fall back to
    "reinforcement" — the same graceful-default precedent
    IntegratedExecutionEngine used for unrecognized capabilities in Step 9.7,
    rather than raising on data that simply predates a newer strategy."""
    engine_cls = _STRATEGIES.get(policy.strategy, ReinforcementLearningPolicy)
    params = policy.parameters

    if engine_cls is PassiveLearningPolicy:
        return PassiveLearningPolicy()

    if engine_cls is ConservativeLearningPolicy:
        threshold = params.get("confidence_threshold", 0.3)
        return ConservativeLearningPolicy(confidence_threshold=threshold)

    return ReinforcementLearningPolicy()


def apply_learning_policy(experience: LearningExperience, policy: LearningPolicy = LearningPolicy()) -> LearningResult:
    """Module-level convenience wrapper around resolve_policy(policy).learn()."""
    return resolve_policy(policy).learn(experience)
