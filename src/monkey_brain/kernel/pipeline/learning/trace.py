"""Learning Trace & Explainability (Step 10.9) — the capstone artifact
narrating the full pipeline: Experience Capture -> Reward Evaluation ->
Belief Update -> World Model Update -> Learning Policy -> Φ Compilation,
matching Step 10's own acceptance-criteria flow verbatim.

Mirrors Step 9.8's ExecutionTrace / build_execution_trace() /
IntegratedExecutionEngine.execute_with_trace() shape exactly: a standalone,
opt-in diagnostic built from pieces the pipeline already computes, not
wired into the hot path (LearningIntegratedPolicy's stage overrides stay
exactly as Step 10.7/10.8 left them — every cycle keeps paying only for
`state.learning`/`state.phi`, not for a trace, unless a caller explicitly
asks for one via learn_with_trace()).

No recomputation: Step 10.6's policies.py was extended (this step) to
carry each stage's already-computed breakdown forward in
LearningResult.metadata ("reward_breakdown", "belief_updates",
"world_updates" -- see policies.py's updated docstring/comments), so this
module reads them back rather than re-deriving reward/belief/world
updates a second time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningPolicy,
    LearningResult,
)
from src.monkey_brain.kernel.pipeline.learning.policies import resolve_policy
from src.monkey_brain.kernel.pipeline.learning.phi import (
    PhiArtifact,
    PhiCompiler,
    phi_to_dict,
)


@dataclass(frozen=True)
class LearningTraceStage:
    """One narrated step of the pipeline."""

    name: str = ""
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningTrace:
    """The full explainable record of one learning cycle."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    experience_id: str = ""
    stages: tuple[LearningTraceStage, ...] = ()
    result: LearningResult = field(default_factory=LearningResult)
    phi: PhiArtifact = field(default_factory=PhiArtifact)
    narrative: str = ""
    compiled_at: float = field(default_factory=time.time)

    def explain(self) -> str:
        return self.narrative

    def to_dict(self) -> dict[str, Any]:
        import dataclasses

        return {
            "trace_id": self.trace_id,
            "experience_id": self.experience_id,
            "stages": [dataclasses.asdict(s) for s in self.stages],
            "result": dataclasses.asdict(self.result),
            "phi": phi_to_dict(self.phi),
            "narrative": self.narrative,
            "compiled_at": self.compiled_at,
        }


def _fmt_belief_update(u: dict[str, Any]) -> str:
    label = f"{u['entity']} {u['attribute'].replace('_', ' ')}"
    verb = "Increase" if u["delta"] > 0 else "Decrease"
    return f"{verb} confidence that {label} ({u['previous_confidence']:.2f} -> {u['new_confidence']:.2f})"


def _fmt_world_update(u: dict[str, Any]) -> str:
    label = f"{u['entity']} {u['attribute'].replace('_', ' ')}"
    verb = "reinforced" if u["delta"] > 0 else "weakened"
    return f"{label} relationship {verb} ({u['previous_strength']:.2f} -> {u['new_strength']:.2f})"


def build_learning_trace(
    experience: LearningExperience,
    policy: LearningPolicy = LearningPolicy(),
    *,
    result: LearningResult | None = None,
    phi: PhiArtifact | None = None,
) -> LearningTrace:
    """Assembles a LearningTrace narrating the full pipeline. If result/phi
    are already computed (e.g. by an integrated runtime cycle), pass them
    in to avoid recomputing; otherwise this runs the policy and Φ compiler
    itself, exactly as learn_with_trace() does."""
    if result is None:
        result = resolve_policy(policy).learn(experience)
    if phi is None:
        phi = PhiCompiler().compile(experience, result)

    outcome = experience.outcome
    outcome_word = "achieved" if outcome.goal_achieved else "partially achieved" if outcome.partial else "not achieved"

    capture_summary = f"Goal '{phi.goal_signature}' captured, outcome {outcome_word}"
    capture_stage = LearningTraceStage(
        name="Experience Capture",
        summary=capture_summary,
        detail={"experience_id": experience.experience_id, "outcome": outcome_word},
    )

    reward_breakdown = result.metadata.get("reward_breakdown", {})
    reward_summary = f"reward {result.reward:+.2f}"
    if reward_breakdown.get("rationale"):
        reward_summary += f" ({reward_breakdown['rationale']})"
    reward_stage = LearningTraceStage(name="Reward Evaluation", summary=reward_summary, detail=reward_breakdown)

    belief_updates = result.metadata.get("belief_updates", [])
    if belief_updates:
        belief_summary = "; ".join(_fmt_belief_update(u) for u in belief_updates)
    else:
        belief_summary = f"No belief update applied ({result.rationale})"
    belief_stage = LearningTraceStage(name="Belief Update", summary=belief_summary, detail={"updates": belief_updates})

    world_updates = result.metadata.get("world_updates", [])
    if world_updates:
        world_summary = "; ".join(_fmt_world_update(u) for u in world_updates)
    else:
        world_summary = f"No world update applied ({result.rationale})"
    world_stage = LearningTraceStage(
        name="World Model Update",
        summary=world_summary,
        detail={"updates": world_updates},
    )

    policy_stage = LearningTraceStage(
        name="Learning Policy",
        summary=f"strategy='{policy.strategy}': {result.rationale}",
        detail={"strategy": policy.strategy, "parameters": dict(policy.parameters)},
    )

    phi_summary = phi.outcome_summary
    if phi.top_signal_summary:
        phi_summary += f"; {phi.top_signal_summary}"
    phi_stage = LearningTraceStage(name="Φ Compilation", summary=phi_summary, detail=phi_to_dict(phi))

    stages = (
        capture_stage,
        reward_stage,
        belief_stage,
        world_stage,
        policy_stage,
        phi_stage,
    )

    header = " -> ".join(s.name for s in stages) + " -> Learning Trace"
    body = "\n".join(f"{i + 1}. {s.name}: {s.summary}" for i, s in enumerate(stages))
    narrative = f"{header}\n\n{body}"

    return LearningTrace(
        experience_id=experience.experience_id,
        stages=stages,
        result=result,
        phi=phi,
        narrative=narrative,
    )


def learn_with_trace(experience: LearningExperience, policy: LearningPolicy = LearningPolicy()) -> LearningTrace:
    """Convenience wrapper: run the policy, compile Φ, and build the trace
    in one call. Mirrors Step 9.7/9.8's
    IntegratedExecutionEngine.execute_with_trace() naming and shape."""
    return build_learning_trace(experience, policy)
