"""Execution scheduler (Step 9.3) — dependency-aware execution.

Builds a dependency graph from ExecutionPlan.steps' `dependencies`, detects
violations (a step depending on an unknown step, or on itself) and deadlocks
(a dependency cycle — nothing left can ever become runnable), and produces
an ExecutionSchedule: an ordered sequence of batches, each batch a tuple of
mutually independent step_ids that may run concurrently.

The batch structure is what makes sequential, parallel, and (eventually)
distributed execution all the same schedule, just consumed differently:
    sequential   -> run each batch's steps one at a time
    parallel     -> run each batch's steps concurrently (here: a thread pool,
                    since ExecutionHandler.execute() is synchronous, matching
                    the live ExecutionEngine.execute() Protocol it will
                    eventually sit behind)
    distributed  -> (future) dispatch each batch's steps to different workers;
                    nothing here assumes a single process, only that a batch's
                    steps have no dependencies on each other

.run() executes a schedule against an ExecutionRegistry (Step 9.2),
respecting dependencies: a step whose dependency failed is SKIPPED rather
than executed against a broken precondition. Retry (Step 9.4) is a separate
concern layered on top of one step's own execution, not this scheduler's job.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum

from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionContext,
    ExecutionError,
    ExecutionOutcome,
    ExecutionPlan,
    ExecutionStatus,
    ExecutionStep,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
)


class ExecutionMode(Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class DependencyViolation:
    """A step's dependencies reference something that can never be satisfied."""

    step_id: str = ""
    missing_dependency: str = ""
    message: str = ""


@dataclass(frozen=True)
class ExecutionSchedule:
    """The result of scheduling an ExecutionPlan.

    batches: step_ids grouped into ordered, internally-independent sets.
    Empty if scheduling failed (violations or a deadlock) — check is_valid
    before treating `batches` as executable.
    """

    batches: tuple[tuple[str, ...], ...] = ()
    violations: tuple[DependencyViolation, ...] = ()
    has_deadlock: bool = False
    deadlocked_step_ids: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.violations and not self.has_deadlock


class ExecutionScheduler:
    """Turns an ExecutionPlan's dependency graph into a batched schedule,
    and (optionally) executes it against an ExecutionRegistry."""

    def __init__(self, registry: ExecutionRegistry | None = None) -> None:
        self._registry = registry or ExecutionRegistry()

    # ── Scheduling (pure — no execution) ────────────────────────────────────

    def schedule(self, plan: ExecutionPlan) -> ExecutionSchedule:
        steps_by_id = {s.step_id: s for s in plan.steps}

        violations = self._find_violations(plan.steps, steps_by_id)
        if violations:
            return ExecutionSchedule(violations=violations)

        # Kahn's algorithm, grouped into levels/batches instead of a flat order.
        remaining: dict[str, set[str]] = {s.step_id: set(s.dependencies) for s in plan.steps}
        scheduled: set[str] = set()
        batches: list[tuple[str, ...]] = []

        while remaining:
            ready = tuple(sorted(step_id for step_id, deps in remaining.items() if deps <= scheduled))
            if not ready:
                return ExecutionSchedule(
                    batches=tuple(batches),
                    has_deadlock=True,
                    deadlocked_step_ids=tuple(sorted(remaining.keys())),
                )
            batches.append(ready)
            scheduled.update(ready)
            for step_id in ready:
                del remaining[step_id]

        return ExecutionSchedule(batches=tuple(batches))

    def _find_violations(
        self,
        steps: tuple[ExecutionStep, ...],
        steps_by_id: dict[str, ExecutionStep],
    ) -> tuple[DependencyViolation, ...]:
        violations = []
        for step in steps:
            for dep in step.dependencies:
                if dep == step.step_id:
                    violations.append(
                        DependencyViolation(
                            step_id=step.step_id,
                            missing_dependency=dep,
                            message=f"step '{step.step_id}' depends on itself",
                        )
                    )
                elif dep not in steps_by_id:
                    violations.append(
                        DependencyViolation(
                            step_id=step.step_id,
                            missing_dependency=dep,
                            message=f"step '{step.step_id}' depends on unknown step '{dep}'",
                        )
                    )
        return tuple(violations)

    # ── Execution ────────────────────────────────────────────────────────

    def run(
        self,
        plan: ExecutionPlan,
        context: ExecutionContext | None = None,
        *,
        mode: ExecutionMode = ExecutionMode.SEQUENTIAL,
    ) -> tuple[ExecutionSchedule, tuple[ExecutionOutcome, ...]]:
        """Schedule and execute `plan`. Returns (schedule, outcomes) — if the
        schedule isn't valid (violations or deadlock), outcomes is empty and
        nothing was executed; inspect the schedule to see why."""
        schedule = self.schedule(plan)
        if not schedule.is_valid:
            return schedule, ()

        steps_by_id = {s.step_id: s for s in plan.steps}
        dependents = self._dependents_map(plan.steps)
        ctx = context or ExecutionContext()

        outcomes: dict[str, ExecutionOutcome] = {}
        skipped: set[str] = set()

        for batch in schedule.batches:
            to_run = [steps_by_id[sid] for sid in batch if sid not in skipped]
            for sid in batch:
                if sid in skipped:
                    outcomes[sid] = ExecutionOutcome(
                        step_id=sid,
                        status=ExecutionStatus.SKIPPED,
                        error=ExecutionError(
                            step_id=sid,
                            code="DEPENDENCY_FAILED",
                            message="skipped — a dependency did not succeed",
                            retryable=False,
                        ),
                    )

            if to_run:
                batch_outcomes = (
                    self._run_batch_parallel(to_run, ctx)
                    if mode == ExecutionMode.PARALLEL and len(to_run) > 1
                    else self._run_batch_sequential(to_run, ctx)
                )
                newly_completed = []
                for outcome in batch_outcomes:
                    outcomes[outcome.step_id] = outcome
                    if outcome.success:
                        newly_completed.append(outcome.step_id)
                    else:
                        skipped |= dependents.get(outcome.step_id, set())
                ctx = replace(
                    ctx,
                    completed_step_ids=ctx.completed_step_ids + tuple(newly_completed),
                )

        ordered = tuple(outcomes[sid] for batch in schedule.batches for sid in batch)
        return schedule, ordered

    def _run_batch_sequential(
        self,
        steps: list[ExecutionStep],
        context: ExecutionContext,
    ) -> list[ExecutionOutcome]:
        return [self._registry.dispatch(step, context) for step in steps]

    def _run_batch_parallel(
        self,
        steps: list[ExecutionStep],
        context: ExecutionContext,
    ) -> list[ExecutionOutcome]:
        with ThreadPoolExecutor(max_workers=len(steps)) as pool:
            futures = [pool.submit(self._registry.dispatch, step, context) for step in steps]
            return [f.result() for f in futures]

    def _dependents_map(self, steps: tuple[ExecutionStep, ...]) -> dict[str, set[str]]:
        """step_id -> the set of ALL step_ids (direct and transitive) that
        depend on it — used to skip downstream work when a step fails."""
        direct: dict[str, set[str]] = {s.step_id: set() for s in steps}
        for step in steps:
            for dep in step.dependencies:
                direct.setdefault(dep, set()).add(step.step_id)

        transitive: dict[str, set[str]] = {}

        def expand(step_id: str, seen: set[str]) -> set[str]:
            if step_id in transitive:
                return transitive[step_id]
            result: set[str] = set()
            for child in direct.get(step_id, ()):
                if child in seen:
                    continue  # already-detected cycles are reported as deadlocks, not re-walked here
                result.add(child)
                result |= expand(child, seen | {step_id})
            transitive[step_id] = result
            return result

        return {step_id: expand(step_id, set()) for step_id in direct}
