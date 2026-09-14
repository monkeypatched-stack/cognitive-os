"""Execution monitoring (Step 9.6) — make execution observable.

ExecutionMonitor tracks step status, timing, errors, retry counts, and
output values as execution happens, and exposes them as an ExecutionTimeline
(chronological events), ExecutionProgress (a point-in-time snapshot), and
ExecutionMetrics (Step 9.1's aggregate type, computed here rather than by
hand).

Two ways to feed it, because of one real integration wrinkle:

    .dispatch(step, context) -> ExecutionOutcome
        A drop-in ExecutionRegistry-shaped wrapper — like RetryExecutor
        (Step 9.4) — so ExecutionScheduler(registry=ExecutionMonitor(...))
        observes every step live, with zero changes to scheduler.py. BUT
        ExecutionScheduler.run() builds SKIPPED outcomes for downstream
        steps directly (Step 9.3), without ever calling dispatch() on them —
        so live composition alone never sees skips.

    .observe_all(outcomes)
        Bulk-ingests a complete outcome tuple — e.g. ExecutionScheduler.run()'s
        second return value, which does include SKIPPED steps. Already-seen
        step_ids (from .dispatch()) aren't double-counted. Use this for
        complete post-hoc observability; use .dispatch() composition for
        live progress during execution. Using both together (composing for
        live view, then observe_all() on the final result) gives one
        complete picture with no gaps and no double-counting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionContext,
    ExecutionMetrics,
    ExecutionOutcome,
    ExecutionStatus,
    ExecutionStep,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
)


class TimelineEventType(Enum):
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_SKIPPED = "step_skipped"


@dataclass(frozen=True)
class TimelineEntry:
    step_id: str = ""
    event_type: TimelineEventType = TimelineEventType.STEP_STARTED
    timestamp: float = field(default_factory=time.time)
    detail: str = ""


@dataclass(frozen=True)
class ExecutionTimeline:
    """The chronological record of everything an ExecutionMonitor observed."""

    entries: tuple[TimelineEntry, ...] = ()

    def events_for_step(self, step_id: str) -> tuple[TimelineEntry, ...]:
        return tuple(e for e in self.entries if e.step_id == step_id)

    def duration_seconds(self) -> float:
        """Wall-clock span from the first to the last recorded event."""
        if len(self.entries) < 2:
            return 0.0
        return max(0.0, self.entries[-1].timestamp - self.entries[0].timestamp)


@dataclass(frozen=True)
class ExecutionProgress:
    """A point-in-time snapshot of how far an execution has gotten."""

    total_steps: int = 0
    completed_steps: int = 0
    """succeeded + failed + skipped — i.e. "done," regardless of outcome."""
    succeeded_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0
    in_progress_steps: int = 0
    pending_steps: int = 0

    @property
    def percent_complete(self) -> float:
        if self.total_steps == 0:
            return 100.0
        return round(100.0 * self.completed_steps / self.total_steps, 2)

    @property
    def is_complete(self) -> bool:
        return self.total_steps > 0 and self.completed_steps == self.total_steps


_EVENT_FOR_STATUS = {
    ExecutionStatus.SUCCEEDED: TimelineEventType.STEP_SUCCEEDED,
    ExecutionStatus.FAILED: TimelineEventType.STEP_FAILED,
    ExecutionStatus.SKIPPED: TimelineEventType.STEP_SKIPPED,
}


class ExecutionMonitor:
    """Observes execution as it happens (or after the fact) and answers
    "what's the timeline / progress / metrics so far.\""""

    def __init__(self, registry: Any = None, total_steps: int | None = None) -> None:
        self._registry = registry if registry is not None else ExecutionRegistry()
        self._total_steps = total_steps
        self._entries: list[TimelineEntry] = []
        self._outcomes: dict[str, ExecutionOutcome] = {}
        self._in_progress: set[str] = set()

    def set_total_steps(self, total: int) -> None:
        self._total_steps = total

    # ── Live composition — drop-in ExecutionRegistry shape ──────────────────

    def dispatch(self, step: ExecutionStep, context: ExecutionContext | None = None) -> ExecutionOutcome:
        operator_name = step.operator.name if step.operator is not None else "?"
        self._record(
            step.step_id,
            TimelineEventType.STEP_STARTED,
            f"dispatching '{operator_name}'",
        )
        self._in_progress.add(step.step_id)

        outcome = self._registry.dispatch(step, context)

        self._in_progress.discard(step.step_id)
        self._record_outcome(outcome)
        return outcome

    # ── Post-hoc bulk ingestion ──────────────────────────────────────────

    def observe_all(self, outcomes: tuple[ExecutionOutcome, ...]) -> None:
        for outcome in outcomes:
            if outcome.step_id in self._outcomes:
                continue  # already recorded via dispatch() — don't double-count
            self._record_outcome(outcome)

    def _record_outcome(self, outcome: ExecutionOutcome) -> None:
        self._outcomes[outcome.step_id] = outcome
        event_type = _EVENT_FOR_STATUS.get(outcome.status, TimelineEventType.STEP_FAILED)
        detail = outcome.error.message if outcome.error is not None else ""
        self._record(outcome.step_id, event_type, detail)

    def _record(self, step_id: str, event_type: TimelineEventType, detail: str) -> None:
        self._entries.append(TimelineEntry(step_id=step_id, event_type=event_type, detail=detail))

    # ── Queries ──────────────────────────────────────────────────────────

    def timeline(self) -> ExecutionTimeline:
        return ExecutionTimeline(entries=tuple(self._entries))

    def outcomes(self) -> tuple[ExecutionOutcome, ...]:
        return tuple(self._outcomes.values())

    def progress(self) -> ExecutionProgress:
        succeeded = sum(1 for o in self._outcomes.values() if o.status == ExecutionStatus.SUCCEEDED)
        failed = sum(1 for o in self._outcomes.values() if o.status == ExecutionStatus.FAILED)
        skipped = sum(1 for o in self._outcomes.values() if o.status == ExecutionStatus.SKIPPED)
        completed = succeeded + failed + skipped
        in_progress = len(self._in_progress)
        total = self._total_steps if self._total_steps is not None else completed + in_progress

        return ExecutionProgress(
            total_steps=total,
            completed_steps=completed,
            succeeded_steps=succeeded,
            failed_steps=failed,
            skipped_steps=skipped,
            in_progress_steps=in_progress,
            pending_steps=max(0, total - completed - in_progress),
        )

    def metrics(self) -> ExecutionMetrics:
        outcomes = tuple(self._outcomes.values())
        succeeded = sum(1 for o in outcomes if o.status == ExecutionStatus.SUCCEEDED)
        failed = sum(1 for o in outcomes if o.status == ExecutionStatus.FAILED)
        skipped = sum(1 for o in outcomes if o.status == ExecutionStatus.SKIPPED)
        retried = sum(1 for o in outcomes if o.attempt > 1)
        total_duration = sum(o.duration_seconds for o in outcomes)

        return ExecutionMetrics(
            total_steps=len(outcomes),
            succeeded=succeeded,
            failed=failed,
            retried=retried,
            skipped=skipped,
            total_duration_seconds=round(total_duration, 4),
        )
