"""Planning trace & explainability (Step 8.8) — make planning observable.

PlanningTrace is the complete record of one planning decision: the goal, its
decomposition, every candidate considered, which were rejected and why, the
selected plan, and a human-readable rationale. build_planning_trace()
assembles one from the pieces IntegratedPlanningEngine already computes —
this module structures and explains existing information, it derives no new
judgments of its own.

.explain() renders the trace as the same Goal -> Decompose -> Generate
Candidates -> Validate -> Score -> Select narrative used throughout this
project's examples, for logging/debugging; a future visualization layer can
consume .to_dict() instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.planning.domain import Goal, Plan, PlanCandidate
from src.monkey_brain.kernel.pipeline.planning.decomposition import GoalTree


@dataclass(frozen=True)
class PlanningTrace:
    """The complete, structured record of one planning decision."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    goal: Goal = field(default_factory=Goal)
    subgoal_tree: GoalTree | None = None
    candidate_plans: tuple[PlanCandidate, ...] = ()
    """Every candidate generated, in generation order — accepted and rejected alike."""
    rejected_plans: tuple[PlanCandidate, ...] = ()
    """The subset of candidate_plans where .rejected is True."""
    validation_failures: tuple[str, ...] = ()
    """Every constraint-violation message across all rejected candidates,
    prefixed with the candidate's strategy/id for attribution."""
    selected_plan: Plan | None = None
    """The winning candidate's Plan, or None if nothing was selected (e.g.
    every candidate was rejected)."""
    rationale: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable summary — the form callers who don't want to
        depend on these dataclasses (logs, API responses) should consume."""
        return {
            "trace_id": self.trace_id,
            "goal": {"name": self.goal.name, "description": self.goal.description},
            "subgoal_names": ([c.goal.name for c in self.subgoal_tree.children] if self.subgoal_tree else []),
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "strategy": c.plan.metadata.get("strategy", ""),
                    "cost": c.plan.estimated_cost,
                    "duration": c.plan.estimated_duration,
                    "score": c.score,
                    "rejected": c.rejected,
                    "rejection_reason": c.rejection_reason,
                }
                for c in self.candidate_plans
            ],
            "validation_failures": list(self.validation_failures),
            "selected_strategy": (self.selected_plan.metadata.get("strategy", "") if self.selected_plan else None),
            "selected_score": (
                next(
                    (c.score for c in self.candidate_plans if c.plan is self.selected_plan),
                    None,
                )
                if self.selected_plan
                else None
            ),
            "rationale": self.rationale,
            "created_at": self.created_at,
        }

    def explain(self) -> str:
        """Human-readable narrative: Goal -> Decompose -> Generate Candidates
        -> Validate -> Score -> Select -> Rationale."""
        lines = [f"Goal: {self.goal.name}"]

        if self.subgoal_tree and self.subgoal_tree.children:
            names = ", ".join(c.goal.name for c in self.subgoal_tree.children)
            lines.append(f"Decompose: {names}")
        else:
            lines.append("Decompose: (leaf — no subgoals)")

        if not self.candidate_plans:
            lines.append("Generate Candidates: (none — no registered strategy)")
            lines.append(f"Rationale: {self.rationale}")
            return "\n".join(lines)

        strategies = [c.plan.metadata.get("strategy", c.candidate_id) for c in self.candidate_plans]
        lines.append(f"Generate Candidates: {', '.join(strategies)}")

        checks = " ".join(
            f"{'✓' if not c.rejected else '✗'} {c.plan.metadata.get('strategy', c.candidate_id)}"
            for c in self.candidate_plans
        )
        lines.append(f"Validate: {checks}")

        scores = ", ".join(
            (
                f"{c.plan.metadata.get('strategy', c.candidate_id)}={c.score:.2f}"
                if c.score is not None
                else f"{c.plan.metadata.get('strategy', c.candidate_id)}=unscored"
            )
            for c in self.candidate_plans
        )
        lines.append(f"Score: {scores}")

        selected_name = (
            self.selected_plan.metadata.get("strategy", "") if self.selected_plan else "(none — fallback used)"
        )
        lines.append(f"Select: {selected_name}")
        lines.append(f"Rationale: {self.rationale}")

        return "\n".join(lines)


def build_planning_trace(
    goal: Goal,
    tree: GoalTree | None,
    candidates: tuple[PlanCandidate, ...],
    selected: PlanCandidate | None,
    *,
    rationale: str | None = None,
) -> PlanningTrace:
    """Assemble a PlanningTrace from what IntegratedPlanningEngine already
    computed. Pure aggregation — no new scoring/validation logic."""
    rejected = tuple(c for c in candidates if c.rejected)
    failures = tuple(
        f"{c.plan.metadata.get('strategy', c.candidate_id)}: {c.rejection_reason}"
        for c in rejected
        if c.rejection_reason
    )

    return PlanningTrace(
        goal=goal,
        subgoal_tree=tree,
        candidate_plans=candidates,
        rejected_plans=rejected,
        validation_failures=failures,
        selected_plan=selected.plan if selected is not None else None,
        rationale=(rationale if rationale is not None else _default_rationale(candidates, selected)),
    )


def _default_rationale(candidates: tuple[PlanCandidate, ...], selected: PlanCandidate | None) -> str:
    if not candidates:
        return "No candidates were generated — no strategy registered for this goal."
    if selected is None:
        rejected_count = sum(1 for c in candidates if c.rejected)
        return f"All {rejected_count} candidate(s) were rejected during validation — no eligible plan."

    strategy = selected.plan.metadata.get("strategy", selected.candidate_id)
    eligible = sum(1 for c in candidates if not c.rejected)
    rejected_count = len(candidates) - eligible
    rejected_note = f", {rejected_count} rejected" if rejected_count else ""
    return (
        f"Selected '{strategy}' (score={selected.score:.4f}) — highest score "
        f"among {eligible} valid candidate(s){rejected_note}."
    )
