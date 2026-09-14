"""Execution engine integration (Step 9.7) — the formal execution framework
behind the existing ExecutionEngine Protocol.

IntegratedExecutionEngine satisfies execution.py's ExecutionEngine Protocol
exactly:

    result = execution_engine.execute(actions, context)
        actions: tuple[Action, ...]   (execution.py's simple type)
        context: Any                  (typically contracts.RuntimeContext)
        -> execution.py's ExecutionResult

— the same call CognitiveRuntime._execute_plan already makes (synchronous —
confirmed by reading execution.py directly, not the spec's `execute(plan,
runtime_context)` paraphrase). It is injected the same way ActionExecutor is
(CognitiveRuntime(execution_engine=...)) — no changes to belief_runtime.py,
execution.py, action_executor.py, planner.py, planning_engine.py,
orchestrator.py, or compiler.py.

Internally: Plan -> Scheduler -> Capability Resolution -> Execution Handlers
-> Monitoring -> ExecutionResult, exactly the flow Step 9.7 asks for —
composed from Steps 9.1-9.6 with zero new algorithm code here, only
conversion and wiring.

Backward compatibility, precisely: incoming Action.capability values come
from whatever produced the plan. LLMPlanner produces
generic action names — nothing this engine's
sample handlers (Step 9.2) know how to run. IntegratedPlanningEngine (Step
8.7), by contrast, produces action names straight from Step 8.2's operator
library (Navigate, AcquireItem, ...) — exactly what Step 9.2's sample
handlers DO know. So: if every action's capability has a registered
handler, the full new pipeline runs; if even one doesn't, this engine falls
back to a real ActionExecutor for the WHOLE action list (not a partial mix
— that would fragment dependency/ordering semantics in confusing ways),
producing identical behavior to not having this engine installed at all.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.execution import (
    Action,
    ActionOutcome,
    ExecutionResult as LegacyExecutionResult,
)
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionContext,
    ExecutionOutcome,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionStep,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.scheduler import (
    ExecutionScheduler,
    ExecutionSchedule,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.retry import (
    RecoveryPolicy,
    RetryExecutor,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.resolution import (
    CapabilityResolver,
    ExecutionEnvironment,
    ResolutionReport,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.monitoring import (
    ExecutionMonitor,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.trace import (
    ExecutionTrace,
    build_execution_trace,
)


class IntegratedExecutionEngine:
    """ExecutionEngine-conformant engine wiring Steps 9.1-9.6 together.

    Every collaborator is injectable; `fallback` defaults to a real
    ActionExecutor. `environment` defaults to treating every capability the
    registry's own handlers declare as available — resolution is about
    checking actor/resource/permission/capacity restrictions you actually
    configure, not an opt-in capability allowlist duplicating what the
    registry already knows how to run.
    """

    def __init__(
        self,
        registry: ExecutionRegistry | None = None,
        environment: ExecutionEnvironment | None = None,
        recovery_policies: dict[str, RecoveryPolicy] | None = None,
        fallback: Any = None,
    ) -> None:
        self._registry = registry or ExecutionRegistry()
        env = environment or ExecutionEnvironment(
            available_capabilities=tuple(c.name for c in self._registry.capabilities()),
        )
        self._resolver = CapabilityResolver(environment=env, registry=self._registry)
        self._retry_executor = RetryExecutor(registry=self._registry, recovery_policies=recovery_policies)
        self._monitor = ExecutionMonitor(registry=self._retry_executor)
        self._scheduler = ExecutionScheduler(registry=self._monitor)
        self._fallback = fallback or ActionExecutor()

    async def execute(
        self,
        actions: tuple[Action, ...],
        context: Any = None,
        *,
        execution_graph: Any = None,
    ) -> LegacyExecutionResult:
        """The required ExecutionEngine.execute() signature — now async,
        matching execution.py's own Protocol (Multi-Actor Execution
        Handoff: ActionExecutor.execute() became async so a capability CAN
        be a real async def handle())."""
        result, _ = await self.execute_with_trace(actions, context, execution_graph=execution_graph)
        return result

    async def execute_with_trace(
        self,
        actions: tuple[Action, ...],
        context: Any = None,
        *,
        execution_graph: Any = None,
    ) -> tuple[LegacyExecutionResult, ExecutionTrace]:
        """Same as execute(), but also returns the ExecutionTrace (Step 9.8)
        of how that result was reached — for callers who want it directly
        rather than reconstructing it from the legacy ExecutionResult alone."""
        if not actions:
            result = LegacyExecutionResult(goal_achieved=True)
            trace = build_execution_trace(
                ExecutionRequest(),
                ExecutionSchedule(),
                (),
                rationale="No actions to execute.",
                goal_achieved_override=result.goal_achieved,
            )
            return result, trace

        if execution_graph is not None or not self._all_capabilities_known(actions):
            result = await self._fallback.execute(
                actions,
                context,
                execution_graph=execution_graph,
            )
            trace = build_execution_trace(
                ExecutionRequest(),
                ExecutionSchedule(),
                (),
                rationale="Delegated to ActionExecutor — execution_graph supplied or one or more "
                "actions used a capability the Step 9 registry has no handler for.",
                goal_achieved_override=result.goal_achieved,
            )
            return result, trace

        plan = self._actions_to_plan(actions)
        actor_id = self._extract_actor_id(context)
        request = ExecutionRequest(plan=plan, actor_id=actor_id)
        labels = {a.action_id: a.capability for a in actions if a.action_id}

        resolution = self._resolver.resolve(plan, request)
        if not resolution.resolved:
            result = self._resolution_failure_result(actions, resolution)
            trace = build_execution_trace(
                request,
                ExecutionSchedule(),
                (),
                resolution=resolution,
                step_labels=labels,
                goal_achieved_override=result.goal_achieved,
            )
            return result, trace

        schedule, outcomes = self._scheduler.run(plan, ExecutionContext(request=request))
        self._monitor.observe_all(outcomes)  # close the skip-visibility gap (Step 9.6)

        trace = build_execution_trace(
            request,
            schedule,
            outcomes,
            resolution=resolution,
            timeline=self._monitor.timeline(),
            step_labels=labels,
        )

        if not schedule.is_valid:
            # Shouldn't happen — _actions_to_plan() only ever builds a linear
            # chain, which can't violate or deadlock — but never silently
            # drop a validity problem if one somehow occurred.
            return (
                await self._fallback.execute(
                    actions,
                    context,
                    execution_graph=execution_graph,
                ),
                trace,
            )

        return self._to_legacy_result(outcomes), trace

    # ── Backward-compatibility gate ─────────────────────────────────────

    def _all_capabilities_known(self, actions: tuple[Action, ...]) -> bool:
        return all(self._registry.get(action.capability) is not None for action in actions)

    # ── Conversion: Action -> ExecutionStep (linear chain) ──────────────

    def _actions_to_plan(self, actions: tuple[Action, ...]) -> ExecutionPlan:
        steps: list[ExecutionStep] = []
        previous_id: str | None = None
        for action in actions:
            step = self._action_to_step(action, previous_id)
            steps.append(step)
            previous_id = step.step_id
        return ExecutionPlan(steps=tuple(steps))

    def _action_to_step(self, action: Action, previous_step_id: str | None) -> ExecutionStep:
        # Action.capability names WHICH operator this is; it carries no
        # required_capabilities of its own (that concept postdates this
        # simple type). Derive it from the registered handler's own declared
        # capability, so CapabilityResolver's availability check has
        # something real to check — without this, required_capabilities
        # would always be empty and resolution would vacuously pass no
        # matter what the environment declares available.
        handler = self._registry.get(action.capability)
        required_capabilities = (handler.capability.name,) if handler is not None else ()

        operator = PlanningOperator(
            name=action.capability,
            preconditions=action.preconditions,
            effects=(action.expected_outcome,) if action.expected_outcome else (),
            required_capabilities=required_capabilities,
        )
        return ExecutionStep(
            step_id=action.action_id or uuid4().hex,
            operator=operator,
            parameters=dict(action.parameters),
            dependencies=(previous_step_id,) if previous_step_id else (),
            expected_effects=((action.expected_outcome,) if action.expected_outcome else ()),
            trace_metadata={
                "source_step": action.source_step,
                "planning_confidence": action.confidence,
            },
        )

    def _extract_actor_id(self, context: Any) -> str:
        actor = getattr(context, "actor", None)
        return getattr(actor, "actor_id", "unknown") if actor is not None else "unknown"

    # ── Conversion: ExecutionOutcome -> ActionOutcome / legacy ExecutionResult ──

    def _to_legacy_result(self, outcomes: tuple[ExecutionOutcome, ...]) -> LegacyExecutionResult:
        action_outcomes = tuple(self._to_action_outcome(o) for o in outcomes)
        success_count = sum(1 for o in action_outcomes if o.success)
        failure_count = len(action_outcomes) - success_count
        total_latency_ms = round(sum(o.latency_ms for o in action_outcomes), 2)

        return LegacyExecutionResult(
            actions=action_outcomes,
            success_count=success_count,
            failure_count=failure_count,
            total_latency_ms=total_latency_ms,
            goal_achieved=(failure_count == 0),
        )

    def _to_action_outcome(self, outcome: ExecutionOutcome) -> ActionOutcome:
        return ActionOutcome(
            action_id=outcome.step_id,
            success=outcome.success,
            result=outcome.output,
            error=outcome.error.message if outcome.error is not None else "",
            latency_ms=round(outcome.duration_seconds * 1000, 2),
            metadata={"attempts": outcome.attempt, "status": outcome.status.value},
        )

    def _resolution_failure_result(
        self,
        actions: tuple[Action, ...],
        resolution: ResolutionReport,
    ) -> LegacyExecutionResult:
        message = "; ".join(issue.message for issue in resolution.issues)
        action_outcomes = tuple(
            ActionOutcome(
                action_id=action.action_id,
                success=False,
                error=f"resolution failed: {message}",
            )
            for action in actions
        )
        return LegacyExecutionResult(
            actions=action_outcomes,
            success_count=0,
            failure_count=len(action_outcomes),
            goal_achieved=False,
        )
