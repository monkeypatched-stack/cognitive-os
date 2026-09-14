"""Multi-Scenario Evaluation (Step 11.6) — instead of one Prediction,
generate and rank Scenario A / Scenario B / Scenario C, each with its own
predicted outcome, probability, utility, and assumptions (Step 11.6's own
spec), then recommend one.

Deliberately agnostic about *how* each scenario's trajectory was produced
-- ScenarioEvaluator only consumes SimulationTrajectory (Step 11.3), so it
works equally well fed a baseline plus Step 11.4 counterfactual branches,
or trajectories from any future Step 11.10 prediction policy (Monte Carlo,
Bayesian, ...), without importing counterfactuals.py itself. This is the
same separation Step 9's ExecutionScheduler kept from *how* an
ExecutionHandler produces an outcome.

Reuses Step 11.1's PredictionCandidate/PredictionResult exactly as
designed there: PredictionCandidate's `scenario_label` + `probability`
fields were built specifically to match "Scenario A: Store open, Success
96%" -- this step is what finally populates them from a real
RiskAssessment (Step 11.5) instead of leaving them at defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    Prediction,
    PredictionCandidate,
    PredictionOutcome,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import SimulationTrajectory
from src.monkey_brain.kernel.pipeline.prediction.risk import RiskEngine
from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionKind
from src.monkey_brain.kernel.pipeline.learning.domain import Provenance

DEFAULT_REJECTION_THRESHOLD = 0.15
"""Lowered from 0.3: probability is the product of every step's own
transition probability (RiskEngine._path_probability), so a legitimate
well-formed plan's probability shrinks with plan length by construction
-- confirmed live, a correctly-grounded 5-step grocery purchase
(ProductSelection -> OrderCreation -> OrderConfirmation -> Payment ->
Delivery) computed ~22% and was rejected at 0.3 despite nothing being
wrong with the plan. 0.15 still rejects genuinely bad plans (a single
badly-learned step can still fail it) without structurally penalizing
every multi-step plan for having multiple steps."""


@dataclass(frozen=True)
class ScenarioInput:
    """One candidate future to evaluate: a label plus the trajectory that
    represents it, however it was produced."""

    label: str = ""
    trajectory: SimulationTrajectory = field(default_factory=SimulationTrajectory)
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioParticipation:
    """Answers "did every scenario source that was SUPPOSED to run for
    this prediction actually run" -- the codebase has exactly one
    prediction ALGORITHM (TransitionModel-based deterministic simulation,
    see kernel/pipeline/prediction/integration.py's module docstring for
    why no solver registry exists here); the real ensemble in THIS
    subsystem is which SCENARIO SOURCES (the Baseline trajectory + every
    registered CounterfactualAssumption) were independently run through
    that one algorithm. This record makes that observable without relying
    on logs alone."""

    scenarios_required: tuple[str, ...] = ()
    scenarios_invoked: tuple[str, ...] = ()
    scenarios_succeeded: tuple[str, ...] = ()
    scenarios_failed: tuple[dict[str, str], ...] = ()
    """Each entry: {"label": ..., "error": ...}."""
    aggregation_status: str = "complete"
    """"complete" (every required scenario succeeded), "partial" (some
    succeeded, some failed), or "failed" (none succeeded, or nothing was
    invoked at all)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios_required": list(self.scenarios_required),
            "scenarios_invoked": list(self.scenarios_invoked),
            "scenarios_succeeded": list(self.scenarios_succeeded),
            "scenarios_failed": [dict(f) for f in self.scenarios_failed],
            "aggregation_status": self.aggregation_status,
        }


def build_scenario_participation(
    baseline_label: str,
    assumption_labels: tuple[str, ...],
    branches: tuple[Any, ...],
) -> ScenarioParticipation:
    """Pure. `branches` is duck-typed exactly like scenarios_from_
    counterfactuals() below (`.assumption.description`/`.category`,
    `.error`) -- no counterfactuals.py import (see this module's own
    TestOwnershipBoundary test). The baseline is never itself allowed to
    silently fail here: CounterfactualEngine.simulate_baseline() (unlike
    .branch()) still raises on failure, by design -- a baseline that can't
    be simulated at all means there is no real path to compare anything
    else against, a more fundamental failure than one hypothesis not
    panning out, so it's surfaced as a real exception to the caller rather
    than folded into "partial" here."""
    required = (baseline_label,) + assumption_labels
    invoked: list[str] = [baseline_label]
    succeeded: list[str] = [baseline_label]
    failed: list[dict[str, str]] = []
    for branch in branches:
        assumption = branch.assumption
        label = assumption.description or assumption.category or "Unnamed scenario"
        invoked.append(label)
        error = getattr(branch, "error", "") or ""
        if error:
            failed.append({"label": label, "error": error})
        else:
            succeeded.append(label)

    if len(succeeded) == len(required):
        status = "complete"
    elif succeeded:
        status = "partial"
    else:
        status = "failed"

    return ScenarioParticipation(
        scenarios_required=required,
        scenarios_invoked=tuple(invoked),
        scenarios_succeeded=tuple(succeeded),
        scenarios_failed=tuple(failed),
        aggregation_status=status,
    )


def scenarios_from_counterfactuals(
    baseline_label: str, baseline: SimulationTrajectory, branches: tuple[Any, ...]
) -> tuple[ScenarioInput, ...]:
    """Convenience bridge from Step 11.4's CounterfactualEngine output:
    the baseline trajectory plus every branch, each becoming its own
    ScenarioInput. `branches` is duck-typed (`.assumption.description`/
    `.category`, `.trajectory`) rather than imported as
    counterfactuals.CounterfactualBranch, the same Any-typing this whole
    package uses at its module boundaries.

    A branch whose simulation failed (`.error` set -- see
    CounterfactualEngine.branch()) is skipped here: its `.trajectory` is a
    meaningless empty default, and feeding that into RiskEngine.assess()
    would produce a misleading vacuous result (an empty trajectory reads
    as guaranteed success) rather than an honest "this scenario wasn't
    evaluated." Use build_scenario_participation() (above) alongside this
    call to see which scenarios were skipped and why -- that information
    is not silently lost, just not represented as a fake ScenarioInput."""
    scenarios = [ScenarioInput(label=baseline_label, trajectory=baseline)]
    for branch in branches:
        if getattr(branch, "error", ""):
            continue
        assumption = branch.assumption
        label = assumption.description or assumption.category or "Unnamed scenario"
        assumptions = (assumption.description,) if assumption.description else ()
        scenarios.append(ScenarioInput(label=label, trajectory=branch.trajectory, assumptions=assumptions))
    return tuple(scenarios)


class ScenarioEvaluator:
    """Risk-assesses, ranks, and recommends among multiple candidate
    scenarios for the same plan."""

    def __init__(
        self,
        risk_engine: RiskEngine | None = None,
        rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD,
    ) -> None:
        self._risk_engine = risk_engine or RiskEngine()
        self._rejection_threshold = rejection_threshold

    def evaluate(
        self,
        scenario: ScenarioInput,
        *,
        time_horizon: float = 0.0,
        provenance: Provenance | None = None,
        prediction_id: str | None = None,
    ) -> PredictionCandidate:
        """One scenario -> one risk-assessed PredictionCandidate.

        provenance/prediction_id are optional (default None -> today's
        exact prior behavior: each Prediction mints its own independent
        id and gets an all-default, unattributed Provenance). When
        provided by a real caller (kernel/pipeline/prediction/
        integration.py's live wiring passes the actor's real actor_id/
        execution_id), every Prediction produced by one evaluate_and_
        recommend() call shares the SAME prediction_id, and carries who
        it was really predicted for -- closing a real gap where
        Prediction.provenance was always left empty in the live path."""
        assessment = self._risk_engine.assess(scenario.trajectory)
        prediction_kwargs: dict[str, Any] = dict(
            world_snapshot=scenario.trajectory.final_state.world_state,
            assumptions=scenario.assumptions,
            predicted_outcomes=self._outcomes_from_trajectory(scenario.trajectory),
            confidence=assessment.confidence,
            expected_utility=assessment.expected_reward,
            time_horizon=time_horizon,
        )
        if prediction_id is not None:
            prediction_kwargs["prediction_id"] = prediction_id
        if provenance is not None:
            prediction_kwargs["provenance"] = provenance
        prediction = Prediction(**prediction_kwargs)
        rejected = assessment.probability_of_success < self._rejection_threshold
        rejection_reason = (
            f"Success probability {assessment.probability_of_success:.0%} "
            f"below acceptance threshold {self._rejection_threshold:.0%}"
            if rejected
            else ""
        )
        return PredictionCandidate(
            prediction=prediction,
            scenario_label=scenario.label,
            probability=assessment.probability_of_success,
            rejected=rejected,
            rejection_reason=rejection_reason,
        )

    def evaluate_all(
        self,
        scenarios: tuple[ScenarioInput, ...],
        *,
        time_horizon: float = 0.0,
        provenance: Provenance | None = None,
        prediction_id: str | None = None,
    ) -> tuple[PredictionCandidate, ...]:
        return tuple(
            self.evaluate(
                s,
                time_horizon=time_horizon,
                provenance=provenance,
                prediction_id=prediction_id,
            )
            for s in scenarios
        )

    def rank(self, candidates: tuple[PredictionCandidate, ...]) -> tuple[PredictionCandidate, ...]:
        """Rejected candidates always sort last; among the rest, highest
        success probability first -- matching the acceptance criteria's
        own ranking (96% > 81% > 12%, the 12% one rejected)."""
        return tuple(sorted(candidates, key=lambda c: (c.rejected, -c.probability)))

    def recommend(
        self,
        candidates: tuple[PredictionCandidate, ...],
        *,
        prediction_id: str | None = None,
        participation: ScenarioParticipation | None = None,
    ) -> PredictionResult:
        ranked = self.rank(candidates)
        eligible = [c for c in ranked if not c.rejected]
        selected = eligible[0] if eligible else None

        if selected is not None:
            recommendation = f"Execute {selected.scenario_label}"
        else:
            recommendation = "No viable scenario -- do not execute"

        metadata: dict[str, Any] = {}
        if participation is not None:
            metadata["scenario_participation"] = participation.to_dict()

        return PredictionResult(
            prediction_id=prediction_id or "",
            candidates=ranked,
            selected=selected,
            recommendation=recommendation,
            rationale=self._build_rationale(ranked, selected),
            metadata=metadata,
        )

    def evaluate_and_recommend(
        self,
        scenarios: tuple[ScenarioInput, ...],
        *,
        time_horizon: float = 0.0,
        provenance: Provenance | None = None,
        prediction_id: str | None = None,
        participation: ScenarioParticipation | None = None,
    ) -> PredictionResult:
        """The full pipeline in one call: evaluate every scenario, rank,
        recommend. `prediction_id` (when given) is minted ONCE by the
        caller and shared across every candidate's Prediction AND the
        returned PredictionResult itself -- PredictionResult.prediction_id
        was declared but never actually set before this change."""
        candidates = self.evaluate_all(
            scenarios,
            time_horizon=time_horizon,
            provenance=provenance,
            prediction_id=prediction_id,
        )
        return self.recommend(candidates, prediction_id=prediction_id, participation=participation)

    # ── Internals ────────────────────────────────────────────────────────

    def _outcomes_from_trajectory(self, trajectory: SimulationTrajectory) -> tuple[PredictionOutcome, ...]:
        outcomes = []
        for state in trajectory.states:
            t = state.applied_transition
            if t is None:
                continue
            outcomes.append(
                PredictionOutcome(
                    description=t.description,
                    # Cognitive Loop Verification: this omitted the kind !=
                    # UNKNOWN check transitions.py's own _build_prediction
                    # already applies — an "Unknown effect... (no knowledge
                    # registered)" transition (probability defaults to 0.5,
                    # see _unknown_transition) must never independently read
                    # as success=true; there's no knowledge to claim it.
                    success=t.kind != TransitionKind.UNKNOWN and t.probability >= 0.5,
                    probability=t.probability,
                    utility=0.0,
                    metadata={"kind": t.kind.value, "step_index": state.step_index},
                )
            )
        return tuple(outcomes)

    def _build_rationale(
        self,
        ranked: tuple[PredictionCandidate, ...],
        selected: PredictionCandidate | None,
    ) -> str:
        if not ranked:
            return "No scenarios evaluated."
        summary = ", ".join(
            f"{c.scenario_label} {c.probability:.0%}" + (" (rejected)" if c.rejected else "") for c in ranked
        )
        if selected is None:
            return f"All scenarios rejected: {summary}."
        return f"{selected.scenario_label} has the highest viable success probability ({selected.probability:.0%}). Scenarios considered: {summary}."
