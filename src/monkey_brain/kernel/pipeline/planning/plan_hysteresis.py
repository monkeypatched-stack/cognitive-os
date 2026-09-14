"""Plan hysteresis — score a freshly generated plan against the actor's
persisted Current Plan and decide whether it's worth replacing.

Smallest slice of a larger "planning as optimization, not stateless
generation" redesign: this module does NOT generate multiple candidate
plans (LLMPlanner still returns exactly one plan per tick, unchanged) —
it only scores that one plan and decides keep-vs-replace against
whatever Current Plan the actor already has. Multi-candidate generation
and per-counterparty trust are later phases, deliberately deferred.

Pure logic, no I/O — kernel/pipeline/planning/current_plan_store.py owns
persistence, kernel/pipeline/comparison/integration.py's decide_stage
owns wiring this into the pipeline.

Note: kernel/pipeline/planning/scoring.py already exists but scores the
separate, unwired kernel/pipeline/planning/domain.py::Plan/PlanCandidate
types (zero callers from the live pipeline) — this module deliberately
does not extend it. It scores the real kernel/pipeline/belief_state.py
::Plan type that LLMPlanner actually returns and that flows through
_generate_plan/_execute_plan in production.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# Real, derivable weights only. state.prediction_result (produced by the
# existing Predict stage every tick, before Decide runs) and the Plan's
# own cost/risk fields are genuinely computed today; nothing here is
# fabricated to fill out a bigger formula.
W_PROBABILITY = 0.5
W_EXPECTED_UTILITY = 0.3
W_COST = 0.1
W_RISK = 0.1

# Deliberately NOT scored, and not silently defaulted to some fake value:
#   - trust: no per-counterparty trust score reaches this layer today
#     (kernel/affiliations/trust.py::TrustEngine is actor-pair trust,
#     not wired into commerce/plan scoring at all).
#   - policy_compliance: governance permissions are a hard allow/deny
#     gate at execute time (belief_runtime.py's resolved_permissions
#     check), not a soft score to blend in here.
#   - duration: kernel/pipeline/belief_state.py::Plan/PlanStep has no
#     estimated-duration field at all.
# Adding real weights for these is a later phase, once each has a real
# data source — not before.


def score_plan(plan: Any, prediction_result: dict[str, Any] | None) -> tuple[float, dict[str, float]]:
    """Pure. Reads only what's really computed by the time Decide runs:
    prediction_result["selected"]["probability"] /
    ["prediction"]["expected_utility"] (PredictionResult, serialized by
    kernel/pipeline/prediction/integration.py::prediction_result_to_dict),
    plus plan.cost/plan.risk (belief_state.Plan's own real fields).

    Returns (score, components) — components is stored verbatim on
    CurrentPlanRecord.score_components and surfaced in the Decision
    debugger panel, so every score stays explainable.
    """
    selected = (prediction_result or {}).get("selected") or {}
    probability = float(selected.get("probability", 0.0) or 0.0)
    expected_utility = float((selected.get("prediction") or {}).get("expected_utility", 0.0) or 0.0)
    cost = float(getattr(plan, "cost", 0.0) or 0.0)
    risk = float(getattr(plan, "risk", 0.0) or 0.0)

    score = W_PROBABILITY * probability + W_EXPECTED_UTILITY * expected_utility - W_COST * cost - W_RISK * risk
    components = {
        "probability": probability,
        "expected_utility": expected_utility,
        "cost": cost,
        "risk": risk,
        "score": score,
    }
    return score, components


def hysteresis_margin() -> float:
    """Relative improvement a new plan must clear over the Current Plan
    to replace it — same env-var convention as
    prediction/persistence.py's REDIS_CONNECT_TIMEOUT_SEC. Default 10%:
    don't thrash on marginal improvements."""
    return float(os.getenv("PLAN_HYSTERESIS_MARGIN", "0.10"))


@dataclass(frozen=True)
class HysteresisVerdict:
    action: str  # "keep" | "replace"
    reason: str
    new_score: float
    current_score: float | None
    percent_improvement: float | None


def decide(new_score: float, current: Any | None) -> HysteresisVerdict:
    """current is a CurrentPlanRecord | None (kept as Any here to avoid a
    circular import with current_plan_store — this module stays pure/
    dependency-free). None means no persisted Current Plan exists yet
    (bootstrap, or Redis unavailable) -> always replace, nothing to
    compare against."""
    if current is None:
        return HysteresisVerdict(
            action="replace",
            reason="No existing Current Plan — this becomes the Current Plan.",
            new_score=new_score,
            current_score=None,
            percent_improvement=None,
        )

    current_score = float(current.score)
    # Near-zero baselines make this ratio noisy (a tiny absolute
    # improvement can look like a huge percentage) — acceptable for this
    # slice, flagged here rather than silently treated as exact.
    percent_improvement = (new_score - current_score) / max(abs(current_score), 1e-6)
    margin = hysteresis_margin()

    if percent_improvement >= margin:
        reason = (
            f"Replacing existing plan: new plan scores {new_score:.3f} vs "
            f"current {current_score:.3f} ({percent_improvement:+.0%}), "
            f"at or above the {margin:.0%} hysteresis threshold."
        )
        return HysteresisVerdict(
            action="replace",
            reason=reason,
            new_score=new_score,
            current_score=current_score,
            percent_improvement=percent_improvement,
        )

    reason = (
        f"Kept existing plan: new plan scores {new_score:.3f} vs current "
        f"{current_score:.3f} ({percent_improvement:+.0%}), below the "
        f"{margin:.0%} hysteresis threshold."
    )
    return HysteresisVerdict(
        action="keep",
        reason=reason,
        new_score=new_score,
        current_score=current_score,
        percent_improvement=percent_improvement,
    )
