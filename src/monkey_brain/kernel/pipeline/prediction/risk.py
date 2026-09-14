"""Risk & Uncertainty Analysis (Step 11.5) — quantifies execution
uncertainty for a simulated trajectory: probability of success, expected
reward, execution risk, named uncertainty sources, and real confidence
intervals.

This is the step that resolves the gap Step 11.4 surfaced in its own
docs: SimulationTrajectory.succeeded (11.3) measures confidence in a
prediction, not goal achievement. RiskEngine.probability_of_success is the
honest answer to "how likely is the predicted path to actually occur" --
the product of each applied transition's own probability, not a
confidence flag.

Builds on Step 11.3's SimulationTrajectory (walks trajectory.states'
applied_transition sequence) and composes with Step 11.4's
CounterfactualBranch (assess_branch() reuses assess() on branch.trajectory)
entirely through public APIs.

expose uncertainty throughout the prediction pipeline (Step 11.5's own
spec) is enrich_prediction(): it threads a RiskAssessment's computed
confidence and expected reward back into a Step 11.1 Prediction's
`confidence`/`expected_utility` fields -- both deliberately left as
defaults/unset by Step 11.2, exactly so this step could fill them in with
real numbers instead of guesses.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    Prediction,
    PredictionConfidence,
)
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionKind,
    WorldTransition,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import (
    SimulationState,
    SimulationTrajectory,
)

LOW_PROBABILITY_THRESHOLD = 0.7


@dataclass(frozen=True)
class RiskFactor:
    """One identified source of uncertainty or risk in a trajectory."""

    name: str = ""
    description: str = ""
    severity: float = 0.0
    """0-1: how much this factor should widen the confidence interval /
    contribute to perceived risk if it materializes."""
    category: str = ""
    """"missing_knowledge" | "uncertain_transition" | "low_probability"."""


@dataclass(frozen=True)
class RiskAssessment:
    """The full risk/uncertainty analysis for one simulated trajectory."""

    probability_of_success: float = 0.0
    expected_reward: float = 0.0
    execution_risk: float = 0.0
    risk_factors: tuple[RiskFactor, ...] = ()
    confidence: PredictionConfidence = field(default_factory=PredictionConfidence)
    rationale: str = ""


class RiskEngine:
    """Computes RiskAssessments from simulated trajectories. utility_if_*
    are the caller's own reward scale (defaulting to a simple +1/-1
    baseline, the same "sensible default, fully configurable" precedent
    Step 10.3's RewardWeights set)."""

    def __init__(
        self,
        utility_if_success: float = 1.0,
        utility_if_failure: float = -1.0,
        low_probability_threshold: float = LOW_PROBABILITY_THRESHOLD,
    ) -> None:
        self._utility_if_success = utility_if_success
        self._utility_if_failure = utility_if_failure
        self._low_probability_threshold = low_probability_threshold

    def assess(self, trajectory: SimulationTrajectory) -> RiskAssessment:
        applied = tuple(s.applied_transition for s in trajectory.states if s.applied_transition is not None)

        if not applied:
            confidence = PredictionConfidence(
                point_estimate=1.0,
                lower_bound=1.0,
                upper_bound=1.0,
                rationale="empty plan -- nothing to risk",
            )
            return RiskAssessment(
                probability_of_success=1.0,
                expected_reward=self._utility_if_success,
                execution_risk=0.0,
                confidence=confidence,
                rationale="empty plan: vacuously certain",
            )

        probability_of_success = self._path_probability(trajectory.states)
        expected_reward = (
            probability_of_success * self._utility_if_success
            + (1.0 - probability_of_success) * self._utility_if_failure
        )
        execution_risk = 1.0 - probability_of_success

        risk_factors = self._identify_risk_factors(applied)
        confidence = self._build_confidence(applied, risk_factors)

        return RiskAssessment(
            probability_of_success=probability_of_success,
            expected_reward=expected_reward,
            execution_risk=execution_risk,
            risk_factors=risk_factors,
            confidence=confidence,
            rationale=(
                f"probability of success {probability_of_success:.2f} across {len(applied)} step(s); "
                f"{len(risk_factors)} risk factor(s) identified"
            ),
        )

    def assess_branch(self, branch: Any) -> RiskAssessment:
        """Convenience: risk-assess a Step 11.4 CounterfactualBranch's own
        trajectory, without this module needing to import
        counterfactuals.py (branch is duck-typed via `.trajectory`, same
        reasoning every prior Any-typed field in this package used)."""
        return self.assess(branch.trajectory)

    # ── Internals ────────────────────────────────────────────────────────

    def _path_probability(self, states: tuple[SimulationState, ...]) -> float:
        """The product of each applied transition's own probability --
        the honest joint/AND-chain probability that the whole sequential
        path occurs (see module docstring). Dependency-aware (PlanStep
        .depends_on -> SimulationState.depends_on, additive field, empty
        by default): when a state declares a dependency on an earlier
        step whose OWN transition was not a real success (the identical
        heuristic kernel/pipeline/prediction/scenarios.py::
        _outcomes_from_trajectory already uses: kind != UNKNOWN and
        probability >= 0.5), this step genuinely cannot occur -- its
        contribution is 0.0, not its own raw transition probability,
        rather than being blindly multiplied in as if it always gets
        attempted regardless of what it depends on. For every plan today
        (depends_on always empty), this is mathematically a no-op: the
        loop below falls straight through to the same running product as
        before this change.

        UNKNOWN-kind transitions (no learned history for this
        (goal_key, action_key) pair -- _unknown_transition, transitions.py)
        are excluded from the product entirely, not multiplied in at their
        flat probability=0.5. Confirmed live: a brand-new actor's first
        real order (ProductSelection -> OrderCreation -> PaymentConfirmation
        -> Payment -> OrderConfirmation -> Delivery, only ProductSelection
        had any learned history yet) computed 0.85 * 0.5 * 0.5 * 0.5 * 0.15
        * 0.5 =~ 0.008 -- a well-formed plan rejected by the 30% acceptance
        threshold (scenarios.py) purely because MOST of its steps had never
        been executed before, not because anything about the plan was
        actually predicted to fail. "No information yet" was being treated
        as an equal multiplicative penalty alongside genuinely-learned
        low-probability outcomes, so any plan with a few not-yet-exercised
        steps was mathematically unable to clear the threshold regardless
        of quality -- every actor's early executions would fail this gate
        by construction. UNKNOWN's uncertainty is still fully reported: it
        remains a risk factor (_identify_risk_factors) and still widens the
        confidence interval (_build_confidence) -- only the point
        probability estimate stops being dragged down by steps that
        contribute zero actual evidence either way. A path with EVERY step
        unknown now returns 1.0 (the same "no informative signal, don't
        penalize" convention this method already uses for an empty plan
        just above), leaning on confidence.point_estimate (still 0.0
        whenever any step is UNKNOWN) to honestly flag "this estimate is
        unsupported," instead of silently double-counting that same
        unsupported-ness as a probability penalty too."""
        by_step_index = {s.step_index: s.applied_transition for s in states if s.applied_transition is not None}
        probability = 1.0
        for s in states:
            t = s.applied_transition
            if t is None:
                continue
            if t.kind == TransitionKind.UNKNOWN:
                continue
            if s.depends_on and not self._dependencies_satisfied(s.depends_on, by_step_index):
                # A dependency the model currently rates unlikely already
                # had ITS OWN (low) probability multiplied into `probability`
                # when its own state was processed above (depends_on chains
                # are sequential -- the dependency is always an earlier
                # state in this same loop) -- excluding this step's own
                # contribution too (rather than zeroing the entire path)
                # avoids double-counting that same evidence, while still
                # reflecting "this step probably won't even be reached" the
                # same "no informative signal either way" way UNKNOWN
                # already is, just above. A hard 0.0 here previously turned
                # ONE moderately-low dependency probability into absolute
                # certainty of failure for the WHOLE downstream chain --
                # confirmed live: a single genuinely-observed failure
                # (probability 0.15, one real bad episode) permanently
                # zeroed every later prediction for a normal 6-step
                # purchase plan, since a 0% prediction is rejected and
                # therefore never re-executed to gather better evidence.
                continue
            probability *= t.probability
        return probability

    @staticmethod
    def _dependencies_satisfied(depends_on: tuple[int, ...], by_step_index: dict[int, WorldTransition]) -> bool:
        for dep_index in depends_on:
            dep_transition = by_step_index.get(dep_index)
            if dep_transition is None:
                continue  # referenced step wasn't part of this trajectory -- nothing to contradict
            dep_succeeded = dep_transition.kind != TransitionKind.UNKNOWN and dep_transition.probability >= 0.5
            if not dep_succeeded:
                return False
        return True

    def _identify_risk_factors(self, applied: tuple[WorldTransition, ...]) -> tuple[RiskFactor, ...]:
        factors: list[RiskFactor] = []
        for i, t in enumerate(applied):
            if t.kind == TransitionKind.UNKNOWN:
                factors.append(
                    RiskFactor(
                        name=f"step_{i}_missing_knowledge",
                        description=t.description,
                        severity=1.0,
                        category="missing_knowledge",
                    )
                )
            elif t.kind == TransitionKind.UNCERTAIN:
                factors.append(
                    RiskFactor(
                        name=f"step_{i}_uncertain_transition",
                        description=t.description,
                        severity=round(1.0 - t.confidence, 4),
                        category="uncertain_transition",
                    )
                )
            elif t.probability < self._low_probability_threshold:
                factors.append(
                    RiskFactor(
                        name=f"step_{i}_low_probability",
                        description=t.description,
                        severity=round(1.0 - t.probability, 4),
                        category="low_probability",
                    )
                )
        return tuple(factors)

    def _build_confidence(
        self, applied: tuple[WorldTransition, ...], risk_factors: tuple[RiskFactor, ...]
    ) -> PredictionConfidence:
        point_estimate = min(t.confidence for t in applied)
        spread = min(0.5, 0.1 * len(risk_factors))
        lower_bound = max(0.0, point_estimate - spread)
        upper_bound = min(1.0, point_estimate + spread)
        return PredictionConfidence(
            point_estimate=point_estimate,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            rationale=(
                f"minimum per-step transition confidence, interval widened by "
                f"{len(risk_factors)} identified risk factor(s)"
            ),
            uncertainty_sources=tuple(f.name for f in risk_factors),
        )


def enrich_prediction(prediction: Prediction, assessment: RiskAssessment) -> Prediction:
    """Threads a RiskAssessment's computed confidence and expected reward
    back into a Prediction -- "expose uncertainty throughout the
    prediction pipeline," Step 11.5's own spec. Prediction is frozen, so
    this returns a new instance (the same replace-not-mutate idiom Step
    10.3's RewardEngine.evaluate() used for LearningExperience)."""
    return dataclasses.replace(
        prediction,
        confidence=assessment.confidence,
        expected_utility=assessment.expected_reward,
    )
