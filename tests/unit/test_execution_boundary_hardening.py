"""Regression coverage for the Execution-boundary hardening pass.

Scope-defining finding (research + a Plan-agent validation pass, both
done before writing any code): this codebase has FOUR independently-wired
execution engines, not one. Only ONE of them is downstream of the
plan-hysteresis/prediction work already hardened earlier this session --
`CognitiveRuntime._execute_plan` (kernel/pipeline/belief_runtime.py) ->
`ActionExecutor`/`CapabilityRuntime` (kernel/pipeline/action_executor.py)
-> `CommerceCapabilityBus.discover()` -> `capability.handle()`. That is
the engine this file hardens and tests. The other three (the `/execute`
HTTP route's `Runtime`/`GoalExecutor`; `ExecutionGraph`/`GraphScheduler`/
`ProcessManager`, wired only to `CodeGenRuntime`; and
`kernel/pipeline/execution_runtime/`'s `IntegratedExecutionEngine`, never
instantiated in production at all) are real but structurally disconnected
from this pipeline -- reported in the final report's REMAINING EXECUTION
GAPS, not touched or tested here.

Given that, this engine's real, confirmed shape (verified by reading every
method body in full, not assumed) is: a flat SEQUENTIAL loop over
`plan.steps` (no graph, no parallelism -- both explicitly declined by the
code's own comment at belief_runtime.py:716-724 as a correctness-risk
change), no per-node state enum (only `ActionOutcome.success: bool`), no
retry, no checkpoint, no suspend/resume/cancel. This file's tests map onto
that real structure -- they do not invent graph/retry/checkpoint tests
for machinery that does not exist here.

Per this session's standing convention, this file is written but not
executed by the assistant. Run with:
    python -m pytest tests/unit/test_execution_boundary_hardening.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Plan, PlanStep
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.execution import Action, ActionOutcome
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor


class _StubCapability:
    """A minimal capability -- `handle(args)` returns a fixed result. Not a
    MagicMock: using a real, tiny class means `inspect.iscoroutinefunction`
    (ActionExecutor's real dispatch check) sees exactly what a genuine
    capability implementation looks like."""

    def __init__(self, success: bool = True, error: str = "", result: dict | None = None) -> None:
        self.success = success
        self.error = error
        self.result = result
        self.call_count = 0

    def handle(self, args: dict) -> dict:
        self.call_count += 1
        if self.result is not None:
            return self.result
        return {"success": self.success, "error": self.error}


class _SpyBus:
    """Records every `discover()` call (name + order) -- the canonical
    capability-dispatch path this whole hardening pass verifies is never
    bypassed. `names()` supports ActionExecutor's case-insensitive retry
    lookup; unused here but required by the real call site."""

    def __init__(self, capabilities: dict[str, _StubCapability]) -> None:
        self._capabilities = capabilities
        self.discover_calls: list[str] = []

    def discover(self, name: str):
        self.discover_calls.append(name)
        return self._capabilities.get(name)

    def names(self):
        return list(self._capabilities.keys())


def _plan(steps: tuple[PlanStep, ...], goal: str = "buy groceries") -> Plan:
    return Plan(goal=goal, steps=steps, cost=0.0, confidence=0.8, risk=0.0, planner="llm")


def _state(
    plan: Plan,
    actor_id: str = "arjun",
    execution_id: str = "exec-1",
    resolved_permissions: frozenset = frozenset(),
) -> CognitiveState:
    actor = Actor(actor_id=actor_id, tenant_id="acme")
    belief = BeliefState(actor_id=actor_id, tenant_id="acme")
    belief.plan = plan
    belief.metadata["_resolved_permissions"] = resolved_permissions
    state = CognitiveState(actor=actor, belief=belief)
    state.metrics = {"execution_id": execution_id}
    return state


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Selected plan becomes execution plan.
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectedPlanBecomesExecutionPlan:
    @pytest.mark.asyncio
    async def test_execute_plan_operates_on_the_exact_belief_plan_object(self):
        plan = _plan((PlanStep(action="ProductSelection", description="find milk"),))
        state = _state(plan)
        rt = CognitiveRuntime()
        result_state = await rt._execute_plan(state)
        assert result_state.belief.plan is plan

    @pytest.mark.asyncio
    async def test_actions_correspond_to_plan_steps_in_order(self):
        cap_a = _StubCapability(success=True)
        cap_b = _StubCapability(success=True)
        bus = _SpyBus({"StepA": cap_a, "StepB": cap_b})
        plan = _plan(
            (
                PlanStep(action="StepA", description="a"),
                PlanStep(action="StepB", description="b"),
            )
        )
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)
        assert [a.action_id for a in result_state.actions] == [
            f"{state.actor.actor_id}_step_0",
            f"{state.actor.actor_id}_step_1",
        ]
        # Compilation-hardening pass: _execute_plan now probes each step's
        # capability resolvability via ActionExecutor.resolve_capability
        # (itself backed by bus.discover()) BEFORE dispatch, in addition
        # to the dispatch-time discover() _execute_action already made —
        # so each capability is now discover()'d twice: once at compile,
        # once at dispatch, both in original step order.
        assert bus.discover_calls == ["StepA", "StepB", "StepA", "StepB"]


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Execution identity remains stable.
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionIdentity:
    @pytest.mark.asyncio
    async def test_execution_id_is_stable_across_every_action_and_the_timeline_record(
        self,
    ):
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        from src.monkey_brain.kernel.timeline.entry import TimelineKind

        cap = _StubCapability(success=True)
        bus = _SpyBus({"CheckPantry": cap})
        plan = _plan((PlanStep(action="CheckPantry", description="look"),))
        state = _state(plan, actor_id="identity_actor", execution_id="exec-identity-1")
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert state.metrics["execution_id"] == "exec-identity-1"

        entries = TimelineStore().query(kind=TimelineKind.EXECUTION, actor_id="identity_actor")
        matching = [e for e in entries if e.metadata.get("execution_id") == "exec-identity-1"]
        assert len(matching) == 1
        assert matching[0].metadata["execution_id"] == "exec-identity-1"


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Dependencies are respected / Test 5: Dependent nodes wait.
# (The fix: PlanStep.depends_on -> Action.depends_on -> ActionExecutor
# gating. Confirmed functionally before writing this file: without the
# fix, a step whose dependency failed or was permission-denied would
# silently execute for real and could even silently "succeed" via
# ActionExecutor's simulated-success fallback.)
# ═══════════════════════════════════════════════════════════════════════════


class TestDependenciesAreRespected:
    @pytest.mark.asyncio
    async def test_dependent_step_blocked_when_dependency_fails_capability_never_invoked(
        self,
    ):
        cap_a = _StubCapability(success=False, error="out of stock")
        cap_b = _StubCapability(success=True)
        bus = _SpyBus({"Milk": cap_a, "OrderCreation": cap_b})
        plan = _plan(
            (
                PlanStep(action="Milk", description="find milk"),
                PlanStep(action="OrderCreation", description="place order", depends_on=(0,)),
            )
        )
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert result_state.actions[0].success is False
        assert result_state.actions[1].success is False
        assert "blocked" in result_state.actions[1].error
        assert cap_b.call_count == 0, "dependent capability must NEVER be invoked once its dependency failed"

    @pytest.mark.asyncio
    async def test_dependent_step_blocked_when_dependency_permission_denied(self):
        """A step permission-denied before ever reaching ActionExecutor
        must still block a dependent step -- fail-closed covers "never
        ran" the same as "ran and failed", since a real side effect is
        what's being prevented either way."""
        cap_b = _StubCapability(success=True)
        bus = _SpyBus({"OrderCreation": cap_b})
        plan = _plan(
            (
                PlanStep(
                    action="ReserveFunds",
                    description="reserve",
                    required_permission="perm-finance",
                ),
                PlanStep(action="OrderCreation", description="place order", depends_on=(0,)),
            )
        )
        state = _state(plan, resolved_permissions=frozenset())
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert "Permission denied" in result_state.actions[0].error
        assert result_state.actions[1].success is False
        assert "blocked" in result_state.actions[1].error
        assert cap_b.call_count == 0

    @pytest.mark.asyncio
    async def test_dependent_step_executes_normally_when_dependency_succeeds(self):
        cap_a = _StubCapability(success=True)
        cap_b = _StubCapability(success=True)
        bus = _SpyBus({"Milk": cap_a, "OrderCreation": cap_b})
        plan = _plan(
            (
                PlanStep(action="Milk", description="find milk"),
                PlanStep(action="OrderCreation", description="place order", depends_on=(0,)),
            )
        )
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert result_state.actions[0].success is True
        assert result_state.actions[1].success is True
        assert cap_b.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_depends_on_is_a_no_op_every_existing_plan_is_unaffected(self):
        """Every plan that exists today has depends_on=() on every step --
        this must reproduce identical behavior to before this fix existed."""
        cap_a = _StubCapability(success=False, error="boom")
        cap_b = _StubCapability(success=True)
        bus = _SpyBus({"A": cap_a, "B": cap_b})
        plan = _plan(
            (
                PlanStep(action="A", description="a"),
                PlanStep(action="B", description="b"),  # no depends_on -- independent item
            )
        )
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert result_state.actions[0].success is False
        assert result_state.actions[1].success is True  # B is independent -- still ran despite A's failure
        assert cap_b.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: Independent nodes can execute independently.
# ═══════════════════════════════════════════════════════════════════════════


class TestIndependentNodesExecuteIndependently:
    @pytest.mark.asyncio
    async def test_two_independent_steps_both_run_regardless_of_order_of_outcome(self):
        cap_milk = _StubCapability(success=True)
        cap_eggs = _StubCapability(success=False, error="out of stock")
        bus = _SpyBus({"Milk": cap_milk, "Eggs": cap_eggs})
        plan = _plan(
            (
                PlanStep(action="Milk", description="milk"),
                PlanStep(action="Eggs", description="eggs"),
            )
        )
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert cap_milk.call_count == 1
        assert cap_eggs.call_count == 1
        assert result_state.actions[0].success is True
        assert result_state.actions[1].success is False


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: Node state transitions are valid.
# This engine has no NodeState enum (PENDING/RUNNING/... does not exist at
# this layer -- confirmed by reading action_executor.py in full). The
# closest real invariant: an ActionOutcome is produced exactly once per
# action, and success is never fabricated -- a capability that reports
# failure never produces success=True.
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeOutcomeIntegrity:
    @pytest.mark.asyncio
    async def test_failed_capability_never_produces_a_fabricated_success(self):
        cap = _StubCapability(success=False, error="out of stock")
        bus = _SpyBus({"Eggs": cap})
        plan = _plan((PlanStep(action="Eggs", description="eggs"),))
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert result_state.actions[0].success is False
        assert result_state.actions[0].error == "out of stock"
        assert result_state.execution_result.goal_achieved is False

    @pytest.mark.asyncio
    async def test_capability_raising_an_exception_is_captured_as_failure_not_propagated(
        self,
    ):
        class RaisingCapability:
            def handle(self, args):
                raise RuntimeError("boom")

        bus = _SpyBus({"Broken": RaisingCapability()})
        plan = _plan((PlanStep(action="Broken", description="broken"),))
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert result_state.actions[0].success is False
        assert "boom" in result_state.actions[0].error


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Capability invocation uses the canonical CapabilityBus.
# ═══════════════════════════════════════════════════════════════════════════


class TestCapabilityDispatchIsCanonical:
    @pytest.mark.asyncio
    async def test_execution_only_ever_calls_bus_discover_never_a_direct_instantiation(
        self,
    ):
        cap = _StubCapability(success=True)
        bus = _SpyBus({"ProductSelection": cap})
        plan = _plan((PlanStep(action="ProductSelection", description="select"),))
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        await rt._execute_plan(state)

        # Compilation-hardening pass: one discover() from the compile-time
        # resolve_capability probe, one from actual dispatch — see the
        # identical note on test_actions_correspond_to_plan_steps_in_order.
        # The invariant this test actually guards (bus.discover is the
        # ONLY path to a capability, never a direct instantiation) still
        # holds; it now just fires twice per real step.
        assert bus.discover_calls == ["ProductSelection", "ProductSelection"]
        assert cap.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: Node output reaches dependent node.
# Real, concrete "buy 2 liters of whole milk" chain: ProductSelection's
# real result -> project_action_result_to_context -> OrderCreation reads
# context["selected_product"] back. Uses grocery.py's real capability bus
# and a real KnowledgeGraph, not a synthetic stub, per the task's explicit
# request for a small deterministic fresh-execution trace.
# ═══════════════════════════════════════════════════════════════════════════


class TestOutputPropagation:
    @pytest.mark.asyncio
    async def test_buy_two_liters_of_whole_milk_output_flows_from_selection_to_order(
        self,
    ):
        from src.monkey_brain.kernel.domains.grocery import (
            build_default_capability_bus,
            project_action_result_to_context,
        )
        from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

        kg = KnowledgeGraph()
        kg.add_entity(
            "prod_milk",
            EntityType.ASSET,
            "Whole Milk (2L)",
            {"price": 4.5, "quantity": 20},
        )

        bus = build_default_capability_bus()
        executor = ActionExecutor(capability_bus=bus, context_projector=project_action_result_to_context)

        plan = _plan(
            (
                PlanStep(
                    action="ProductSelection",
                    description="select milk",
                    parameters={"selection": [{"id": "prod_milk", "qty": 2}]},
                    confidence=0.9,
                ),
                PlanStep(
                    action="OrderCreation",
                    description="create order",
                    depends_on=(0,),
                    confidence=0.9,
                ),
            ),
            goal="buy 2 liters of whole milk",
        )

        state = _state(plan, actor_id="milk_buyer", execution_id="exec-milk-trace")
        state.context = {
            "knowledge_graph": kg,
            "actor_id": "milk_buyer",
            "question": "Buy 2 liters of whole milk",
        }
        rt = CognitiveRuntime(execution_engine=executor)
        result_state = await rt._execute_plan(state)

        er = result_state.execution_result
        assert er.actions[0].success is True
        assert er.actions[0].result["selected"][0]["id"] == "prod_milk"
        assert er.actions[1].success is True
        # OrderCreation genuinely read context["selected_product"] (set by
        # the projector from step 0's "selected" key), not a fabricated
        # empty order -- confirmed by a real order_id and matching totals.
        assert er.actions[1].result["order_id"] is not None
        assert len(er.actions[1].result["items"]) == 1
        assert er.actions[1].result["items"][0]["qty"] == 2
        assert er.success_count == 2 and er.failure_count == 0
        assert er.goal_achieved is True


# ═══════════════════════════════════════════════════════════════════════════
# Test 9/10: Capability failure produces correct node failure / failed node
# does not incorrectly produce success.
# ═══════════════════════════════════════════════════════════════════════════


class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_a_to_b_to_c_b_fails_c_independent_still_runs_result_is_partial(self):
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        from src.monkey_brain.kernel.timeline.entry import TimelineKind

        cap_a = _StubCapability(success=True)
        cap_b = _StubCapability(success=False, error="out of stock")
        cap_c = _StubCapability(success=True)
        bus = _SpyBus({"A": cap_a, "B": cap_b, "C": cap_c})
        plan = _plan(
            (
                PlanStep(action="A", description="a"),
                PlanStep(action="B", description="b"),
                PlanStep(action="C", description="c"),
            )
        )
        state = _state(plan, actor_id="partial_actor", execution_id="exec-partial-1")
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        er = result_state.execution_result
        assert er.actions[0].success is True
        assert er.actions[1].success is False
        assert er.actions[2].success is True
        assert er.success_count == 2 and er.failure_count == 1
        assert er.goal_achieved is False  # not falsely reported as full success

        entries = TimelineStore().query(kind=TimelineKind.EXECUTION, actor_id="partial_actor")
        matching = [e for e in entries if e.metadata.get("execution_id") == "exec-partial-1"]
        assert len(matching) == 1
        assert matching[0].outcome == "partial"  # not collapsed into a single boolean


# ═══════════════════════════════════════════════════════════════════════════
# Test 11: Partial execution remains PARTIAL (all three tri-state values).
# ═══════════════════════════════════════════════════════════════════════════


class TestPartialExecutionTriState:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcomes,expected",
        [
            ((True, True), "success"),
            ((False, False), "failure"),
            ((True, False), "partial"),
        ],
    )
    async def test_outcome_tri_state_matches_actual_results(self, outcomes, expected):
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        from src.monkey_brain.kernel.timeline.entry import TimelineKind

        cap_a = _StubCapability(success=outcomes[0])
        cap_b = _StubCapability(success=outcomes[1])
        bus = _SpyBus({"A": cap_a, "B": cap_b})
        plan = _plan(
            (
                PlanStep(action="A", description="a"),
                PlanStep(action="B", description="b"),
            )
        )
        execution_id = f"exec-tristate-{expected}"
        state = _state(plan, actor_id=f"tristate_{expected}", execution_id=execution_id)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        await rt._execute_plan(state)

        entries = TimelineStore().query(kind=TimelineKind.EXECUTION, actor_id=f"tristate_{expected}")
        matching = [e for e in entries if e.metadata.get("execution_id") == execution_id]
        assert len(matching) == 1
        assert matching[0].outcome == expected


# ═══════════════════════════════════════════════════════════════════════════
# Test 12: Retry semantics -- NOT IMPLEMENTED at this layer. No test is
# written asserting absence (not meaningful coverage); confirmed instead
# by reading kernel/pipeline/action_executor.py:128-225 in full: no
# loop, no backoff, no attempt counter, no re-invocation of a failed
# capability exists anywhere in this file. A single ActionOutcome is
# produced per action, once, always.
# ═══════════════════════════════════════════════════════════════════════════


class TestRetryIsCalledExactlyOnce:
    """Not "retry works" (it doesn't exist) -- the real, testable
    invariant this engine DOES guarantee: a capability is invoked exactly
    once, whether it succeeds or fails, never more."""

    @pytest.mark.asyncio
    async def test_capability_invoked_exactly_once_on_failure(self):
        cap = _StubCapability(success=False, error="fails every time")
        bus = _SpyBus({"Flaky": cap})
        plan = _plan((PlanStep(action="Flaky", description="flaky"),))
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        await rt._execute_plan(state)

        assert cap.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Test 13: Completed nodes do not execute twice. This engine has no
# restart/resume (single non-yielding coroutine call per tick), so the
# only real, testable form of this invariant is: a blocked-by-dependency
# action never invokes its capability at all (verified above,
# TestDependenciesAreRespected), and every action in the dispatched tuple
# produces exactly one outcome -- no duplicate ActionOutcomes for the
# same action_id.
# ═══════════════════════════════════════════════════════════════════════════


class TestNoDuplicateExecution:
    @pytest.mark.asyncio
    async def test_no_duplicate_outcomes_for_the_same_action(self):
        cap_a = _StubCapability(success=True)
        cap_b = _StubCapability(success=True)
        bus = _SpyBus({"A": cap_a, "B": cap_b})
        plan = _plan(
            (
                PlanStep(action="A", description="a"),
                PlanStep(action="B", description="b"),
            )
        )
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        action_ids = [a.action_id for a in result_state.actions]
        assert len(action_ids) == len(set(action_ids))
        assert cap_a.call_count == 1 and cap_b.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Test 14: Execution completion requires terminal state for every node --
# for this engine, "terminal" means every dispatched action has a real
# ActionOutcome; the loop cannot return early leaving some actions
# unaccounted for.
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionCompletion:
    @pytest.mark.asyncio
    async def test_every_action_produces_an_outcome_none_silently_dropped(self):
        caps = {name: _StubCapability(success=(name != "C")) for name in ("A", "B", "C", "D")}
        bus = _SpyBus(caps)
        plan = _plan(tuple(PlanStep(action=name, description=name) for name in ("A", "B", "C", "D")))
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        assert len(result_state.actions) == len(plan.steps) == 4
        assert result_state.execution_result.success_count + result_state.execution_result.failure_count == 4


# ═══════════════════════════════════════════════════════════════════════════
# Test 15: Observation represents actual execution result.
# ═══════════════════════════════════════════════════════════════════════════


class TestObservationFidelity:
    @pytest.mark.asyncio
    async def test_observed_outcome_reflects_a_real_failure_not_fabricated_success(
        self,
    ):
        cap = _StubCapability(success=False, error="capability genuinely failed")
        bus = _SpyBus({"Eggs": cap})
        plan = _plan((PlanStep(action="Eggs", description="eggs"),))
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)
        result_state = await rt._observe_outcome(result_state)

        assert result_state.outcome["failure_count"] == 1
        assert result_state.outcome["goal_achieved"] is False


# ═══════════════════════════════════════════════════════════════════════════
# Test 18: Two executions remain isolated (concurrent, different actors,
# different plans, different failure patterns -- a focused two-execution
# test per the task's explicit instruction, not a full concurrency
# benchmark).
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionIsolation:
    @pytest.mark.asyncio
    async def test_two_concurrent_executions_do_not_cross_contaminate(self):
        async def run_one(execution_id, actor_id, fail):
            cap_x = _StubCapability(success=True)
            cap_y = _StubCapability(success=not fail)
            bus = _SpyBus({"X": cap_x, "Y": cap_y})
            plan = _plan(
                (
                    PlanStep(action="X", description="x"),
                    PlanStep(action="Y", description="y"),
                )
            )
            state = _state(plan, actor_id=actor_id, execution_id=execution_id)
            rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
            return await rt._execute_plan(state)

        state_a, state_b = await asyncio.gather(
            run_one("exec-iso-A", "iso_alice", fail=False),
            run_one("exec-iso-B", "iso_bob", fail=True),
        )

        assert state_a.metrics["execution_id"] == "exec-iso-A"
        assert state_b.metrics["execution_id"] == "exec-iso-B"
        assert all(a.success for a in state_a.execution_result.actions)
        assert state_b.execution_result.actions[1].success is False
        # The critical isolation assertion: B's failure must not leak into A.
        assert state_a.execution_result.actions[1].success is True


# ═══════════════════════════════════════════════════════════════════════════
# Test 20: Final execution result is traceable to the executed graph
# (here: the executed flat step sequence -- every ActionOutcome maps back
# to a real plan.steps entry by position).
# ═══════════════════════════════════════════════════════════════════════════


class TestResultTraceableToExecutedPlan:
    @pytest.mark.asyncio
    async def test_each_outcome_traces_back_to_its_originating_plan_step(self):
        cap_a = _StubCapability(success=True, result={"success": True, "picked": "milk"})
        cap_b = _StubCapability(success=True, result={"success": True, "picked": "eggs"})
        bus = _SpyBus({"PickMilk": cap_a, "PickEggs": cap_b})
        plan = _plan(
            (
                PlanStep(action="PickMilk", description="milk"),
                PlanStep(action="PickEggs", description="eggs"),
            )
        )
        state = _state(plan)
        rt = CognitiveRuntime(execution_engine=ActionExecutor(capability_bus=bus))
        result_state = await rt._execute_plan(state)

        for step, outcome in zip(plan.steps, result_state.actions):
            assert outcome.result["picked"] == ("milk" if step.action == "PickMilk" else "eggs")
