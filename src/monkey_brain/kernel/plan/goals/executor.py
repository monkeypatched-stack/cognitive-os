"""Goal Executor — single unified entry point for all goal execution."""

from __future__ import annotations

import logging
from typing import Any, Tuple

from src.monkey_brain.kernel.plan.goals.goal import Goal, GoalType
from src.monkey_brain.kernel.plan.goals.intent_ir import validate_intent_ir
from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.models import ExecutionMode

logger = logging.getLogger("agentos.executor")

# QUERY and QA_BACKGROUND are read-only modes: a goal classified as a
# mutating action must never run for real under either, regardless of which
# strategy would otherwise resolve it. (SIMULATION never reaches this class —
# /predict's simulate route uses PredictEngine directly, not UnifiedExecutor.)
_MUTATING_GOAL_TYPES = frozenset({GoalType.CREATE, GoalType.UPDATE, GoalType.DELETE})
_READ_ONLY_MODES = frozenset({ExecutionMode.QUERY, ExecutionMode.QA_BACKGROUND})


class GoalExecutor:
    """Unified resolver that handles all goal execution through a single interface."""

    def __init__(self) -> None:
        """Execute only an explicit planner-produced workload."""

    async def execute_context(
        self,
        context: ExecutionContext,
        mongo_client: Any,
        **kwargs: Any,
    ) -> Tuple[str, list, list, bool]:
        """Runtime entry point: Intent IR → validation → preconditions → execution.

        This is the context-first interface the architecture calls for —
        no loose (goal, run_id, mode, intent) parameter list. Everything the
        runtime needs comes from `context`. The runtime never classifies
        natural language here; it only validates and executes the IntentIR
        the planner already produced.
        """
        log_extra = context.log_extra()
        ir = context.intent_ir
        if ir is None:
            logger.error(
                "run=%r execute_context called with no IntentIR",
                context.run_id,
                extra=log_extra,
            )
            return ("Error executing goal: no IntentIR provided", [], [], False)

        # Validation must stop execution immediately on failure — never fall
        # back to reclassifying the question from execution_metadata.
        errors = validate_intent_ir(ir)
        if errors:
            logger.warning(
                "run=%r IntentIR validation failed: %s",
                context.run_id,
                "; ".join(errors),
                extra=log_extra,
            )
            return (
                f"Error executing goal: IntentIR validation failed — {'; '.join(errors)}",
                [],
                [],
                False,
            )

        goal = Goal.from_dict(dict(ir.goal))
        question = ir.execution_metadata.get("question", "")
        plan_steps = kwargs.pop("plan_steps", [])

        precondition_errors = self._check_preconditions(goal)
        if precondition_errors:
            logger.warning(
                "run=%r precondition check failed for goal %r: %s",
                context.run_id,
                goal.name,
                "; ".join(precondition_errors),
                extra=log_extra,
            )
            return (
                f"Error executing goal {goal.name}: precondition check failed — {'; '.join(precondition_errors)}",
                [],
                [],
                False,
            )

        # Defense-in-depth authorization at the execution layer (see
        # _authorize). Independent of the API edge's require_permission, so a
        # caller reaching the runtime any other way is still gated.
        authz_error = await self._authorize(goal, context)
        if authz_error:
            logger.warning(
                "run=%r authorization denied for goal %r: %s",
                context.run_id,
                goal.name,
                authz_error,
                extra=log_extra,
            )
            return (f"Error executing goal {goal.name}: {authz_error}", [], [], False)

        return await self._run_goal(
            goal,
            mongo_client,
            question,
            mode=context.execution_mode,
            run_id=context.run_id,
            plan_steps=plan_steps,
            log_extra=context.log_extra(),
        )

    @staticmethod
    def _check_preconditions(goal: Goal) -> list[str]:
        """Execution preconditions checked before any mutation is attempted.

        This is a well-formedness gate, not an authorization gate — the goal
        must be routable. Authorization is handled separately by _authorize()
        (execution-layer, defense in depth) and by the API edge's
        require_permission dependency. There is no entity/resource registry in
        this codebase yet to check entity existence or environmental
        readiness against — flagged as a gap, not silently assumed away.
        """
        errors: list[str] = []
        if not goal.name:
            errors.append("goal.name is required")
        if not isinstance(goal.goal_type, GoalType):
            errors.append(f"goal.goal_type is not a valid GoalType: {goal.goal_type!r}")
        return errors

    @staticmethod
    async def _authorize(goal: Goal, context: ExecutionContext) -> str | None:
        """Execution-layer authorization — defense in depth behind the API edge.

        The API's require_permission dependency gates the HTTP entry point, but
        that is a single perimeter: anything reaching the runtime another way
        (an internal caller, a future RPC, a bypassed edge) would otherwise
        execute unchecked. This re-authorizes the resolved goal against OPA at
        the point of execution, keyed on the caller identity, the goal's action
        (CREATE/UPDATE/DELETE/QUERY), and the target resource.

        Inert only when COGNITIVEOS_ALLOW_INSECURE_DEV_MODE is set (and
        AGENTOS_OPA_ENFORCE is not forced on). Default: fail-closed OPA.
        """
        from src.monkey_brain.kernel.production_gates import opa_enforce_execution
        from src.monkey_brain.kernel.security_boundary import build_opa_input

        if not opa_enforce_execution():
            return None
        try:
            from services.common.opa import evaluate
        except Exception as exc:
            logger.error(
                "run=%r AGENTOS_OPA_ENFORCE is on but the OPA client is unavailable: %s",
                context.run_id,
                exc,
            )
            return "authorization unavailable and enforcement is enabled"

        action = getattr(goal.goal_type, "value", str(goal.goal_type))
        input_data = build_opa_input(action=action, resource=goal.name)
        input_data["principal"] = {
            "sub": input_data["auth"].get("principal") or getattr(context, "user_id", ""),
        }
        try:
            # default_allow=False: with enforcement on, an unconfigured or
            # unreachable OPA denies rather than waves the request through.
            allowed = await evaluate("agentos/execute/allow", input_data, default_allow=False)
        except Exception as exc:
            logger.error("run=%r authorization evaluation failed: %s", context.run_id, exc)
            return "authorization check failed"
        if not allowed:
            return "authorization denied by policy"
        return None

    async def execute(
        self,
        goal: Goal,
        mongo_client: Any,
        question: str,
        **kwargs: Any,
    ) -> Tuple[str, list, list, bool]:
        """Execute a goal through the unified resolution pipeline (loose-param form).

        Prefer execute_context() for new call sites — this form still backs
        callers (qa.py, healing_helpers.py, prompt_helpers.py,
        simulate_helpers.py) that haven't been migrated to build an
        ExecutionContext yet.

        Returns:
            (answer, semantic_hits, graph_paths, llm_answered)
        """
        run_id = kwargs.get("run_id", "")
        mode = kwargs.get("mode")
        plan_steps = kwargs.get("plan_steps") or []
        from types import SimpleNamespace

        authz_error = await self._authorize(
            goal,
            SimpleNamespace(user_id=kwargs.get("user_id", ""), run_id=run_id),
        )
        if authz_error:
            return (f"Error executing goal {goal.name}: {authz_error}", [], [], False)
        return await self._run_goal(
            goal,
            mongo_client,
            question,
            mode=mode,
            run_id=run_id,
            plan_steps=plan_steps,
        )

    async def _run_goal(
        self,
        goal: Goal,
        mongo_client: Any,
        question: str,
        *,
        mode: Any,
        run_id: str,
        plan_steps: list,
        log_extra: dict | None = None,
    ) -> Tuple[str, list, list, bool]:
        """Shared resolution pipeline used by both execute() and execute_context().

        log_extra: structured fields (run_id, trace_id, execution_mode, goal_id,
        intent_id, schema_version) from ExecutionContext.log_extra() — attached
        to every log call here instead of being passed in one at a time.
        execute()'s loose-param callers don't have a context, so they only get
        run_id in log_extra.
        """
        log_extra = log_extra or {"run_id": run_id}
        try:
            # Read-only enforcement: read-only modes must never execute a mutating goal.
            if mode in _READ_ONLY_MODES and goal.goal_type in _MUTATING_GOAL_TYPES:
                logger.warning(
                    "run=%r refusing goal %r (goal_type=%s) — mutating actions are not permitted in %s mode",
                    run_id,
                    goal.name,
                    goal.goal_type.value,
                    getattr(mode, "value", mode),
                    extra=log_extra,
                )
                return (
                    f"Error executing goal {goal.name}: "
                    f"{goal.goal_type.value} actions are not permitted in {getattr(mode, 'value', mode)} mode (read-only)",
                    [],
                    [],
                    False,
                )

            # Strategy 1: Build a Workload from plan_steps provided by /plan → /execute flow
            if plan_steps:
                from src.monkey_brain.kernel.plan.workload.workload import (
                    Workload,
                    WorkloadStep,
                )
                from src.monkey_brain.kernel.execute.runtime.state import ExecutionState

                def _to_step(s) -> WorkloadStep:
                    if isinstance(s, WorkloadStep):
                        return s
                    if isinstance(s, str):
                        return WorkloadStep(capability_name=s)
                    metadata = {}
                    if s.get("step_type"):
                        metadata["step_type"] = s["step_type"]
                    # step_id/dependencies carry the planner's DAG through to the
                    # runtime, which must not run a step before its prerequisites.
                    kwargs = {}
                    if s.get("step_id"):
                        kwargs["step_id"] = s["step_id"]
                    return WorkloadStep(
                        capability_name=s.get("capability_name", s.get("name", s.get("capability", ""))),
                        agent_name=s.get("agent_name", s.get("agent", "")),
                        dependencies=list(s.get("dependencies", [])),
                        metadata=metadata,
                        **kwargs,
                    )

                planned_workload = Workload(steps=[_to_step(s) for s in plan_steps])
                state = ExecutionState(
                    question=question,
                    intent=goal.name,
                    trace_id=run_id,
                )

                from src.monkey_brain.runtime.runtime import Runtime
                from src.monkey_brain.kernel.security_boundary import (
                    SecurityBoundaryDenied,
                    run_governed_mutation,
                )
                from src.monkey_brain.kernel.audit import AuditPersistenceError

                runtime = Runtime()
                mutating = goal.goal_type in _MUTATING_GOAL_TYPES

                async def _mutate():
                    return await runtime.execute(planned_workload, state)

                try:
                    if mutating:
                        exec_result = await run_governed_mutation(
                            action=getattr(goal.goal_type, "value", str(goal.goal_type)),
                            resource=goal.name,
                            mutate=_mutate,
                            extra={"run_id": run_id, "question": question[:200]},
                            skip_authz=True,
                        )
                    else:
                        exec_result = await _mutate()
                except SecurityBoundaryDenied as exc:
                    logger.error(
                        "run=%r governed mutation denied: %s",
                        run_id,
                        exc,
                        extra=log_extra,
                    )
                    return (f"Error executing goal {goal.name}: {exc}", [], [], False)
                except AuditPersistenceError as exc:
                    logger.error(
                        "run=%r audit persistence error: %s",
                        run_id,
                        exc,
                        extra=log_extra,
                    )
                    return (
                        f"Error executing goal {goal.name}: audit failure",
                        [],
                        [],
                        False,
                    )
                except Exception as hitl_exc:
                    # Check if this is a HumanApprovalRequired exception (HITL flow)
                    if hitl_exc.__class__.__name__ == "HumanApprovalRequired":
                        logger.info(
                            "run=%r operation requires human approval (approval_id=%s)",
                            run_id,
                            getattr(hitl_exc, "approval_id", "unknown"),
                            extra=log_extra,
                        )
                        # Return special marker that caller can use to poll for approval
                        approval_id = getattr(hitl_exc, "approval_id", "")
                        operation_id = getattr(hitl_exc, "operation_id", "")
                        return (
                            f"Operation {operation_id} is awaiting human approval. "
                            f"Use approval_id={approval_id} to track status.",
                            [],
                            [],
                            False,
                        )
                    # Not a HITL exception, re-raise
                    raise

                result = (
                    exec_result.final_state.get("answer", f"{goal.name} executed successfully"),
                    exec_result.final_state.get("semantic_hits", []),
                    exec_result.final_state.get("graph_paths", []),
                    exec_result.final_state.get("llm_answered", exec_result.success),
                )
                return result

            return (
                f"Error executing goal {goal.name}: no planner-produced workload supplied",
                [],
                [],
                False,
            )

        except Exception as e:
            logger.error(
                "run=%r goal resolution failed for %s: %s",
                run_id,
                goal.name,
                e,
                extra=log_extra,
            )
            return (
                f"Error executing goal {goal.name}: {str(e)}",
                [],
                [],
                False,
            )


def get_goal_executor() -> GoalExecutor:
    return GoalExecutor()
