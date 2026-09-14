"""Execution trace & explainability (Step 9.8) — make execution observable.

ExecutionTrace is the complete record of one execution: what was scheduled,
what actually ran, which steps retried or failed or were skipped, their
outputs and timing, and the final result. build_execution_trace() assembles
one from what IntegratedExecutionEngine (Step 9.7) already computes — this
module structures and explains existing information, deriving no new
judgments, exactly mirroring planning/trace.py (Step 8.8).

.explain() renders the trace as the Execution Request -> Dependency
Scheduling -> Capability Resolution -> Execute -> Execution Monitoring ->
Execution Result narrative from Step 9's own acceptance criteria; a future
visualization layer can consume .to_dict() instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionMetrics,
    ExecutionOutcome,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.scheduler import (
    ExecutionSchedule,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.resolution import (
    ResolutionReport,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.monitoring import (
    ExecutionTimeline,
)


@dataclass(frozen=True)
class ExecutionTrace:
    """The complete, structured record of one execution."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    request_id: str = ""
    schedule: ExecutionSchedule | None = None
    resolution: ResolutionReport | None = None
    outcomes: tuple[ExecutionOutcome, ...] = ()
    """Every executed (or skipped) step, in schedule order."""
    retried_step_ids: tuple[str, ...] = ()
    failed_step_ids: tuple[str, ...] = ()
    skipped_step_ids: tuple[str, ...] = ()
    timeline: ExecutionTimeline | None = None
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    final_result: ExecutionResult = field(default_factory=ExecutionResult)
    step_labels: dict[str, str] = field(default_factory=dict)
    """Optional step_id -> human-readable label, for .explain() output."""
    rationale: str = ""
    created_at: float = field(default_factory=time.time)

    def _label(self, step_id: str) -> str:
        return self.step_labels.get(step_id, step_id[:8])

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable summary — the form callers who don't want to
        depend on these dataclasses (logs, API responses) should consume."""
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "schedule": (
                {
                    "batches": [list(b) for b in self.schedule.batches],
                    "is_valid": self.schedule.is_valid,
                    "has_deadlock": self.schedule.has_deadlock,
                }
                if self.schedule
                else None
            ),
            "resolution": (
                {
                    "resolved": self.resolution.resolved,
                    "issues": [
                        {"kind": i.kind, "subject": i.subject, "message": i.message} for i in self.resolution.issues
                    ],
                }
                if self.resolution
                else None
            ),
            "outcomes": [
                {
                    "step_id": o.step_id,
                    "label": self._label(o.step_id),
                    "status": o.status.value,
                    "output": o.output,
                    "error": o.error.message if o.error else None,
                    "attempt": o.attempt,
                    "duration_seconds": o.duration_seconds,
                }
                for o in self.outcomes
            ],
            "retried_step_ids": list(self.retried_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
            "skipped_step_ids": list(self.skipped_step_ids),
            "metrics": {
                "total_steps": self.metrics.total_steps,
                "succeeded": self.metrics.succeeded,
                "failed": self.metrics.failed,
                "retried": self.metrics.retried,
                "skipped": self.metrics.skipped,
                "success_rate": self.metrics.success_rate,
                "total_duration_seconds": self.metrics.total_duration_seconds,
            },
            "goal_achieved": self.final_result.goal_achieved,
            "rationale": self.rationale,
            "created_at": self.created_at,
        }

    def explain(self) -> str:
        """Human-readable narrative: Execution Request -> Dependency
        Scheduling -> Capability Resolution -> Execute -> Execution
        Monitoring -> Execution Result."""
        lines = ["Execution Request", "    ↓"]

        batch_note = (
            f" ({len(self.schedule.batches)} batch{'es' if len(self.schedule.batches) != 1 else ''})"
            if self.schedule
            else ""
        )
        lines.append(f"Dependency Scheduling{batch_note}")
        if self.schedule is not None and not self.schedule.is_valid:
            if self.schedule.has_deadlock:
                lines.append(
                    f"  ✗ deadlock among: {', '.join(self._label(s) for s in self.schedule.deadlocked_step_ids)}"
                )
            for v in self.schedule.violations:
                lines.append(f"  ✗ {v.message}")
        lines.append("    ↓")

        resolved = self.resolution is None or self.resolution.resolved
        lines.append(f"Capability Resolution {'✓' if resolved else '✗'}")
        if self.resolution is not None and not self.resolution.resolved:
            for issue in self.resolution.issues:
                lines.append(f"  ✗ {issue.kind}: {issue.message}")
        lines.append("    ↓")

        lines.append("Execute:")
        for outcome in self.outcomes:
            if outcome.status == ExecutionStatus.SUCCEEDED:
                mark = "✓"
            elif outcome.status == ExecutionStatus.SKIPPED:
                mark = "⤳"
            else:
                mark = "✗"
            retry_note = f" (attempt {outcome.attempt})" if outcome.attempt > 1 else ""
            lines.append(f"  {mark} {self._label(outcome.step_id)}{retry_note}")
        lines.append("    ↓")

        lines.append("Execution Monitoring")
        lines.append(
            f"  {self.metrics.succeeded}/{self.metrics.total_steps} succeeded, "
            f"{self.metrics.failed} failed, {self.metrics.skipped} skipped, {self.metrics.retried} retried"
        )
        lines.append("    ↓")

        lines.append(f"Execution Result: {'goal achieved' if self.final_result.goal_achieved else 'goal not achieved'}")
        lines.append(f"Rationale: {self.rationale}")

        return "\n".join(lines)


def build_execution_trace(
    request: ExecutionRequest,
    schedule: ExecutionSchedule,
    outcomes: tuple[ExecutionOutcome, ...],
    *,
    resolution: ResolutionReport | None = None,
    timeline: ExecutionTimeline | None = None,
    metrics: ExecutionMetrics | None = None,
    step_labels: dict[str, str] | None = None,
    rationale: str | None = None,
    goal_achieved_override: bool | None = None,
) -> ExecutionTrace:
    """Assemble an ExecutionTrace from what IntegratedExecutionEngine
    already computed. Pure aggregation — no new scheduling/resolution/retry
    logic.

    goal_achieved_override: when the caller already knows the real outcome
    from elsewhere (e.g. a fallback engine's own result, or the "empty
    actions is vacuous success" case) and `outcomes` is empty precisely
    because the new pipeline never ran, use this instead of deriving
    goal_achieved from an empty outcome set — which would otherwise always
    say False regardless of what actually happened.
    """
    retried = tuple(o.step_id for o in outcomes if o.attempt > 1)
    failed = tuple(o.step_id for o in outcomes if o.status == ExecutionStatus.FAILED)
    skipped = tuple(o.step_id for o in outcomes if o.status == ExecutionStatus.SKIPPED)

    resolved_metrics = metrics if metrics is not None else _metrics_from_outcomes(outcomes)
    if goal_achieved_override is not None:
        goal_achieved = goal_achieved_override
    else:
        goal_achieved = resolved_metrics.total_steps > 0 and resolved_metrics.failed == 0
    final_result = ExecutionResult(
        request_id=request.request_id,
        outcomes=outcomes,
        metrics=resolved_metrics,
        goal_achieved=goal_achieved,
    )

    return ExecutionTrace(
        request_id=request.request_id,
        schedule=schedule,
        resolution=resolution,
        outcomes=outcomes,
        retried_step_ids=retried,
        failed_step_ids=failed,
        skipped_step_ids=skipped,
        timeline=timeline,
        metrics=resolved_metrics,
        final_result=final_result,
        step_labels=dict(step_labels) if step_labels else {},
        rationale=(
            rationale if rationale is not None else _default_rationale(schedule, resolution, outcomes, resolved_metrics)
        ),
    )


def _metrics_from_outcomes(outcomes: tuple[ExecutionOutcome, ...]) -> ExecutionMetrics:
    succeeded = sum(1 for o in outcomes if o.status == ExecutionStatus.SUCCEEDED)
    failed = sum(1 for o in outcomes if o.status == ExecutionStatus.FAILED)
    skipped = sum(1 for o in outcomes if o.status == ExecutionStatus.SKIPPED)
    retried = sum(1 for o in outcomes if o.attempt > 1)
    return ExecutionMetrics(
        total_steps=len(outcomes),
        succeeded=succeeded,
        failed=failed,
        retried=retried,
        skipped=skipped,
        total_duration_seconds=round(sum(o.duration_seconds for o in outcomes), 4),
    )


def _default_rationale(
    schedule: ExecutionSchedule,
    resolution: ResolutionReport | None,
    outcomes: tuple[ExecutionOutcome, ...],
    metrics: ExecutionMetrics,
) -> str:
    if not schedule.is_valid:
        reason = "a dependency deadlock" if schedule.has_deadlock else "dependency violations"
        return f"Execution did not start — {reason} in the schedule."
    if resolution is not None and not resolution.resolved:
        return f"Execution did not start — resolution failed: {'; '.join(i.message for i in resolution.issues)}."
    if not outcomes:
        return "No steps to execute."
    if metrics.failed == 0 and metrics.skipped == 0:
        return f"All {metrics.total_steps} step(s) succeeded" + (
            f" ({metrics.retried} required a retry)." if metrics.retried else "."
        )
    return (
        f"{metrics.succeeded}/{metrics.total_steps} step(s) succeeded, "
        f"{metrics.failed} failed, {metrics.skipped} skipped as a result."
    )
