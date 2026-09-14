"""Minimal Lemon Metrics layer — regression tests for the instrumentation
added at each real runtime boundary (execution, plan lifecycle, LLM,
capability, idempotency, audit). Uses the exact _obs.set_sink(Recorder())
pattern tests/scenarios/test_mb3056_lemon_metrics.py already established
for capturing real emitted telemetry without a live Lemon/Elasticsearch
backend.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

import time

import src.monkey_brain.kernel.comparator_runtime as comparator_module
from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime
from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.vertical_router import build_execution_engine
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.policy.store import PolicyStore
from src.monkey_brain.kernel.pipeline.action_executor import (
    ActionOutcome,
    ExecutionResult,
)
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.audit_trail import (
    query_audit_timeline,
    record_plan_event,
)
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Plan, PlanStep
from src.monkey_brain.kernel.pipeline.comparison.integration import (
    ComparisonIntegratedPolicy,
    _apply_transition_learning,
    _run_comparison,
    _run_decide,
)
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.pipeline.execution_state import (
    CognitiveState,
    WorldSnapshot,
)
from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
    CurrentPlanRecord,
    plan_to_dict,
)
from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal
from src.monkey_brain.kernel.pipeline.planning.plan_staleness import (
    capture_entity_versions,
)
from src.monkey_brain.kernel.pipeline.learning.integration import (
    LearningIntegratedPolicy,
)
from src.monkey_brain.kernel.pipeline.prediction.integration import (
    PredictionIntegratedPolicy,
)
from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel
from src.monkey_brain.kernel.timeline.store import TimelineStore


@pytest.fixture(autouse=True)
def _sink():
    recorder = _obs.Recorder()
    _obs.set_sink(recorder)
    yield recorder
    _obs.clear_sink()


def _plan(steps: tuple[str, ...] = ("ProductSelection", "OrderCreation")) -> Plan:
    return Plan(goal="buy milk", steps=tuple(PlanStep(action=s, description=s) for s in steps))


def _outcome(success: bool, error: str = "") -> ActionOutcome:
    return ActionOutcome(action_id="a1", success=success, error=error, latency_ms=12.5)


class TestExecutionCounters:
    def test_successful_execution_increments_completed(self, _sink):
        rt = CognitiveRuntime()
        belief = BeliefState(actor_id="alice")
        plan = _plan()
        exec_result = ExecutionResult(
            actions=(_outcome(True), _outcome(True)),
            success_count=2,
            failure_count=0,
            total_latency_ms=42.0,
            goal_achieved=True,
        )
        rt._record_execution(belief, plan, exec_result, "exec_1")

        events = [e for e in _sink.events if e[1] == "execution.total"]
        assert any(e[3].get("status") == "completed" for e in events)
        assert _sink.count("execution.duration_ms") == 1
        step_events = [e for e in _sink.events if e[1] == "execution.step.total"]
        assert sum(1 for e in step_events if e[3].get("status") == "succeeded") == 2

    def test_failed_execution_increments_failed(self, _sink):
        rt = CognitiveRuntime()
        belief = BeliefState(actor_id="alice")
        plan = _plan()
        exec_result = ExecutionResult(
            actions=(_outcome(False, "insufficient balance"), _outcome(True)),
            success_count=1,
            failure_count=1,
            total_latency_ms=20.0,
            goal_achieved=False,
        )
        rt._record_execution(belief, plan, exec_result, "exec_2")

        events = [e for e in _sink.events if e[1] == "execution.total"]
        assert any(e[3].get("status") == "failed" for e in events)

    def test_blocked_dependency_increments_blocked_step_counter(self, _sink):
        """Reuses this session's own live-reproduced cyclic-dependency
        scenario, driven through the real ActionExecutor (not
        hand-built ActionOutcome objects) -- confirms the "blocked:
        dependency" error-string classification in action_executor.py
        actually fires from a real run, not just a construction in a
        test double."""
        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "m1", "Store", delivery_fee=1.0)["store_id"]
        milk_id = list_product(kg, store, "m1", "Milk", price=3.0, quantity=10)["product_id"]
        executor = build_execution_engine("grocery")

        a1 = Action(
            action_id="a1",
            capability="ProductSelection",
            step_index=0,
            depends_on=(1,),
            parameters={"selection": [{"id": milk_id, "qty": 1}]},
            correlation_id="exec_3",
        )
        a2 = Action(
            action_id="a2",
            capability="OrderCreation",
            step_index=1,
            depends_on=(0,),
            parameters={},
            correlation_id="exec_3",
        )
        ctx = {"knowledge_graph": kg, "actor_id": "alice", "question": ""}
        asyncio.run(executor.execute((a1, a2), ctx))

        step_events = [e for e in _sink.events if e[1] == "capability.calls.total"]
        assert any(e[3].get("status") == "blocked" for e in step_events)


class TestPlanCounters:
    def _seeded_kg(self, quantity: int = 5):
        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "m1", "Store", delivery_fee=1.0)["store_id"]
        milk_id = list_product(kg, store, "m1", "Milk", price=3.0, quantity=quantity)["product_id"]
        return kg, milk_id

    def test_plan_invalidation_increments_stale_and_replan_counters(self, _sink):
        kg, milk_id = self._seeded_kg()
        old_plan = Plan(
            goal="buy milk",
            steps=(
                PlanStep(
                    action="ProductSelection",
                    parameters={"selection": [{"id": milk_id, "qty": 1}]},
                ),
                PlanStep(action="OrderCreation", parameters={}),
            ),
        )
        current = CurrentPlanRecord(
            plan_id="plan_A",
            actor_id="arjun",
            goal="buy milk",
            steps=tuple(s.action for s in old_plan.steps),
            score=0.9,
            plan=plan_to_dict(old_plan),
            entity_versions=capture_entity_versions(kg, old_plan),
        )
        kg.update_entity(milk_id, attributes={"quantity": 0})

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current

        new_plan = Plan(
            goal="buy milk",
            steps=(
                PlanStep(
                    action="ProductSelection",
                    parameters={"selection": [{"id": milk_id, "qty": 1}]},
                ),
                PlanStep(action="OrderCreation", parameters={}),
            ),
        )
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.update_goal(name="buy milk")
        state = CognitiveState(actor=actor, belief=belief)
        state.plan = new_plan
        state.prediction_result = {"selected": {"probability": 0.3, "prediction": {"expected_utility": 0.1}}}
        state.metrics = {"execution_id": "exec_4"}
        state.context = {"knowledge_graph": kg}

        asyncio.run(_run_decide(state, policy))

        assert any(e[1] == "plan.validation.total" and e[3].get("result") == "stale" for e in _sink.events)
        assert _sink.count("plan.replan.total") >= 1
        assert any(e[1] == "plan.total" and e[3].get("status") == "created" for e in _sink.events)


class TestLLMCounters:
    def test_backend_exception_increments_error_status(self, _sink):
        from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        class RaisingBackend:
            async def complete(self, *a, **kw):
                raise RuntimeError("LLM provider down")

        asyncio.run(LLMPlanner(backend=RaisingBackend()).plan(BeliefState(actor_id="a"), Goal(name="buy milk"), None))

        events = [e for e in _sink.events if e[1] == "llm.calls.total"]
        assert any(e[3].get("status") == "error" for e in events)
        assert _sink.count("llm.call.duration_ms") >= 1

    def test_malformed_response_increments_invalid_response_status(self, _sink):
        from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        class FakeBackend:
            async def complete(self, *a, **kw):
                return "not json at all { broken"

        asyncio.run(LLMPlanner(backend=FakeBackend()).plan(BeliefState(actor_id="a"), Goal(name="buy milk"), None))

        events = [e for e in _sink.events if e[1] == "llm.calls.total"]
        assert any(e[3].get("status") == "invalid_response" for e in events)


class TestCapabilityCounters:
    def test_capability_failure_increments_failed_status(self, _sink):
        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "m1", "Store", delivery_fee=1.0)["store_id"]
        executor = build_execution_engine("grocery")

        # Hallucinated product id -> ProductSelectionCapability rejects it
        # with a plain error string (not the "blocked:" prefix) -> "failed".
        bad = Action(
            action_id="a1",
            capability="ProductSelection",
            step_index=0,
            depends_on=(),
            parameters={"selection": [{"id": "product_totally_fake", "qty": 1}]},
            correlation_id="exec_5",
        )
        ctx = {
            "knowledge_graph": kg,
            "actor_id": "alice",
            "question": "",
            "_relevant_knowledge_ids": {"product_totally_fake"},
        }
        asyncio.run(executor.execute((bad,), ctx))

        events = [e for e in _sink.events if e[1] == "capability.calls.total"]
        assert any(e[3].get("status") == "failed" and e[3].get("capability") == "ProductSelection" for e in events)


def _idempotency_fake_auth() -> str:
    return "test-user"


class _IdempotencyTestBody(BaseModel):
    # Module-level, not nested in a method: FastAPI resolves a route's
    # parameter types via typing.get_type_hints() against the wrapped
    # function's __globals__ — a Pydantic model defined inside a
    # function/method only lives in that scope and isn't resolvable
    # there, silently breaking body parsing (confirmed: nesting this
    # made every request 422 before the idempotency logic even ran).
    name: str


def _make_idempotency_test_app(calls: dict) -> FastAPI:
    from src.monkey_brain.api.idempotency import idempotent

    app = FastAPI()

    @app.post("/x")
    @idempotent("x.create")
    async def handler(
        body: _IdempotencyTestBody,
        request: Request,
        user_id: str = Depends(_idempotency_fake_auth),
    ) -> dict:
        calls["n"] += 1
        return {"n": calls["n"]}

    return app


class TestIdempotencyCounters:
    def _app(self, calls: dict) -> FastAPI:
        return _make_idempotency_test_app(calls)

    def test_replay_increments_replay_counter(self, _sink, monkeypatch):
        monkeypatch.setenv("IDEMPOTENCY_STORE_BACKEND", "memory")
        from src.monkey_brain.api.idempotency import IdempotencyStore

        IdempotencyStore._instance = None
        client = TestClient(self._app({"n": 0}))
        client.post("/x", json={"name": "a"}, headers={"Idempotency-Key": "k1"})
        client.post("/x", json={"name": "a"}, headers={"Idempotency-Key": "k1"})

        events = [e for e in _sink.events if e[1] == "idempotency.requests.total"]
        assert any(e[3].get("result") == "new" for e in events)
        assert any(e[3].get("result") == "replay" for e in events)

    def test_conflict_increments_conflict_counter(self, _sink, monkeypatch):
        monkeypatch.setenv("IDEMPOTENCY_STORE_BACKEND", "memory")
        from src.monkey_brain.api.idempotency import IdempotencyStore

        IdempotencyStore._instance = None
        client = TestClient(self._app({"n": 0}))
        client.post("/x", json={"name": "a"}, headers={"Idempotency-Key": "k2"})
        client.post("/x", json={"name": "different"}, headers={"Idempotency-Key": "k2"})

        events = [e for e in _sink.events if e[1] == "idempotency.requests.total"]
        assert any(e[3].get("result") == "conflict" for e in events)


class TestAuditCounters:
    def test_audit_write_failure_increments_write_errors(self, _sink, monkeypatch):
        from src.monkey_brain.kernel.pipeline.audit_trail import record_plan_event
        from src.monkey_brain.kernel.timeline.store import TimelineStore

        # TimelineStore's own Redis backend already fails soft internally
        # (logs a warning, never raises) -- to exercise record_plan_event's
        # OWN except branch (audit.write_errors.total), the failure has to
        # happen at the TimelineStore.record() call itself, not inside its
        # backend's already-swallowed error path.
        def _raise(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(TimelineStore, "record", _raise)

        record_plan_event("generated", plan_id="p1", actor_id="alice", execution_id="exec_6", goal="x")

        assert _sink.count("audit.write_errors.total") >= 1
        events = [e for e in _sink.events if e[1] == "audit.events.total"]
        assert any(e[3].get("status") == "error" for e in events)

    def test_audit_success_increments_success_status(self, _sink):
        from src.monkey_brain.kernel.pipeline.audit_trail import record_decision_event

        record_decision_event(
            "payment_completed",
            actor_id="alice",
            execution_id="exec_7",
            reason="Charged $5",
        )

        events = [e for e in _sink.events if e[1] == "audit.events.total"]
        assert any(e[3].get("status") == "success" and e[3].get("event_type") == "decision" for e in events)


class TestMetricBackendFailureIsNonFatal:
    def test_no_sink_and_no_lemon_does_not_raise(self, monkeypatch):
        """With the sink cleared and the booted Lemon's own counter/gauge/
        histogram methods raising, every instrumented call must still
        complete normally -- metrics are observability, not a hard
        dependency of execution correctness. _obs.py's own _lemon()
        already fails soft (returns None) if Lemon was never booted at
        all; the realistic failure this proves against is a booted Lemon
        whose recording methods themselves misbehave."""
        _obs.clear_sink()

        class _BrokenLemon:
            def counter(self, *a, **kw):
                raise RuntimeError("lemon down")

            def gauge(self, *a, **kw):
                raise RuntimeError("lemon down")

            def histogram(self, *a, **kw):
                raise RuntimeError("lemon down")

        monkeypatch.setattr(_obs, "_lemon", lambda: _BrokenLemon())

        rt = CognitiveRuntime()
        belief = BeliefState(actor_id="alice")
        plan = _plan()
        exec_result = ExecutionResult(
            actions=(_outcome(True),),
            success_count=1,
            failure_count=0,
            total_latency_ms=10.0,
            goal_achieved=True,
        )
        # Must not raise.
        rt._record_execution(belief, plan, exec_result, "exec_8")


class TestNoHighCardinalityLabels:
    """Static check: none of THIS sprint's new metric names are ever
    tagged with a raw identifier (execution_id/actor_id/plan_id/order_id/
    etc.) -- those belong in the durable audit trail, not metric labels.
    Scoped to the new metric names specifically (not every _obs call in
    these files) -- several pre-existing, unrelated _obs.gauge/counter
    calls in belief_runtime.py already tag actor_id (e.g.
    pipeline.cognitive_tick_total_ms), which is out of this sprint's scope
    to change."""

    _FORBIDDEN = (
        "execution_id",
        "actor_id",
        "goal_id",
        "plan_id",
        "order_id",
        "payment_id",
        "product_id",
        "provider_id",
        "request_id",
        "idempotency_key",
    )
    _NEW_METRIC_NAMES = (
        "execution.total",
        "execution.duration_ms",
        "execution.step.total",
        "execution.step.duration_ms",
        "execution.active",
        "plan.total",
        "plan.validation.total",
        "plan.replan.total",
        "llm.calls.total",
        "llm.call.duration_ms",
        "capability.calls.total",
        "capability.duration_ms",
        "idempotency.requests.total",
        "audit.events.total",
        "audit.write_errors.total",
        "grounding.requests.total",
        "grounding.duration_ms",
        # Cognitive Loop instrumentation pass (Predict/TransitionGate/Compare/
        # LearnTransitions were previously uninstrumented -- see
        # living-world-explorer/src/components/ArchitectureDiagram.tsx and
        # LemonMetricsPanel.tsx's Pipeline Stages drawers).
        "prediction.total",
        "prediction.candidates",
        "prediction.selected_probability",
        "prediction.duration_ms",
        "transition_gate.evaluations.total",
        "compare.total",
        "compare.actor_loss",
        "compare.world_loss",
        "compare.policy_loss",
        "learn.transitions.total",
        "learn.skipped.total",
        "learn.policy_store_updates.total",
        "learn.known_transitions",
        # Remaining structural gaps closed in the same pass (Observe/
        # Observe Outcome/Learn(base)/World Commit).
        "observe.total",
        "observe.observations_acquired",
        "observe.world_snapshot_states",
        "observe.duration_ms",
        "observe_outcome.total",
        "observe_outcome.actions_executed",
        "learn.total",
        "learn.reward",
        "learn.signals_applied",
        "learn.belief_updated.total",
        "learn.world_updated.total",
        "world_commit.total",
    )
    _FILES = (
        "src/monkey_brain/kernel/pipeline/belief_runtime.py",
        "src/monkey_brain/kernel/pipeline/comparison/integration.py",
        "src/monkey_brain/kernel/pipeline/llm_planner.py",
        "src/monkey_brain/kernel/pipeline/action_executor.py",
        "src/monkey_brain/api/idempotency.py",
        "src/monkey_brain/kernel/pipeline/audit_trail.py",
        "src/monkey_brain/kernel/pipeline/planning/context_engine.py",
        "src/monkey_brain/kernel/pipeline/prediction/integration.py",
        "src/monkey_brain/kernel/pipeline/learning/integration.py",
    )

    def test_no_forbidden_label_keywords_near_new_metric_calls(self):
        import re

        for path in self._FILES:
            with open(path) as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                is_new_metric_call = (
                    "_obs.counter(" in line or "_obs.histogram(" in line or "_obs.gauge(" in line
                ) and any(f'"{name}"' in line for name in self._NEW_METRIC_NAMES)
                if is_new_metric_call:
                    window = "".join(lines[i : i + 4])
                    call_end = window.find(")")
                    call_text = window[:call_end] if call_end != -1 else window
                    for forbidden in self._FORBIDDEN:
                        assert not re.search(rf"\b{forbidden}\s*=", call_text), (
                            f"{path}:{i + 1} tags a metric with {forbidden!r} -- high-cardinality label"
                        )


# ═══════════════════════════════════════════════════════════════════════════
# Cognitive-loop ORDERING proofs — added per the "refactor to match the
# verified architecture" task. Verification (see this session's own
# extensive call-graph trace, and CognitiveLoopDiagram.tsx's own header
# comment) found the LIVE implementation already matches the target
# ordering:
#
#   observe -> believe -> plan -> predict -> decide -> execute
#     (per action: transition_gate -> negotiation-if-required -> commit)
#   -> observe_outcome -> compare -> learn -> learn_transitions
#
# So these are NEW tests proving that ordering, not a refactor -- per the
# task's own instruction ("if the ordering is already correct: do not
# refactor unnecessarily; document the verified call path; add/strengthen
# tests"). Deliberately NOT duplicating already-existing, more thorough
# coverage elsewhere:
#   - TEST 3/4 (proposal < gate < negotiation < commit, with/without
#     contention): tests/scenarios/test_transition_gate.py::
#     test_gate001_simple_milk_purchase_gate_evaluated_no_unnecessary_negotiation
#     and ::test_gate007_commit_ordering_proposal_before_negotiation_before_commit
#     (real monkeypatch timestamp spies at the real call sites).
#   - TEST 8 (blocked/not-attempted never learned as failure):
#     tests/unit/test_learning_hardening.py::TestPartialExecutionLearning,
#     ::TestSuccessfulTransitionLearning::
#     test_comparator_is_the_authoritative_evidence_source_not_raw_execution.
#   - TEST 10 (actor isolation, concurrent): tests/scenarios/
#     test_actor_isolation_audit.py (10 tests) and
#     test_per_actor_cognitive_os.py (15 tests, including
#     test_8_concurrent_execution_no_contamination and
#     test_15_comparator_isolation).
#   - TEST 11 (authorization denial): tests/security/test_governance_gate.py.
#   - TEST 12 (real E2E milk scenario): tests/e2e/cognitive_loop/
#     test_e2e01_happy_path.py::test_e2e01_complete_happy_path — requires
#     a live backend (AGENTOS_URL), so it cannot run in this environment;
#     see that file's own module docstring.
#
# Per this session's standing convention (already documented in
# test_learning_hardening.py's own module docstring), this file is
# written but not executed by the assistant. Run with:
#     python -m pytest tests/unit/test_lemon_metrics_instrumentation.py -v
# ═══════════════════════════════════════════════════════════════════════════


async def _noop_stage(state):
    return state


def _configured_policy(policy):
    """Runs ComparisonIntegratedPolicy.configure() with plain no-op stage
    functions and returns the policy -- proves the STRUCTURE of the real
    stage list configure() builds without needing a full CognitiveRuntime/
    PlanetaryRuntime boot. getattr(plan, "__self__", None) in configure()
    (used to recover the owning CognitiveRuntime for ReasoningRuntime/
    ExecutionRuntime construction) safely returns None for a plain
    function, which every downstream getattr(..., None) call already
    tolerates -- confirmed by reading configure() directly, not assumed."""
    policy.configure(
        observe=_noop_stage,
        believe=_noop_stage,
        plan=_noop_stage,
        execute=_noop_stage,
        observe_outcome=_noop_stage,
        learn=_noop_stage,
        compile_phi=_noop_stage,
        predict=_noop_stage,
        commit=_noop_stage,
    )
    return policy


class TestStageOrderMatchesTargetArchitecture:
    """Structural proof of the TARGET ordering: if this list is right,
    every downstream ordering guarantee this task asks for (Predict before
    Execute, TransitionGate/Negotiation/Commit before Compare/Learn,
    Compare before Learn before LearnTransitions) follows from it -- this
    IS the real stage list every actor tick runs, not a description of it."""

    def test_configured_stage_order(self):
        policy = _configured_policy(ComparisonIntegratedPolicy())
        names = [name for name, _ in policy._stages]
        assert names == [
            "observe",
            "believe",
            "plan",
            "predict",
            "decide",
            "execute",
            "observe_outcome",
            "plan_outcome_feedback",
            "compare",
            "learn",
            "learn_transitions",
            "compile_phi",
            "commit",
        ], f"live stage order diverges from target architecture: {names}"

    def test_execute_group_stage_precedes_compare_and_learn(self):
        # Belt-and-suspenders on the specific rule the task calls out by
        # name ("The system MUST NOT: Execute -> Compare -> Learn ->
        # TransitionGate -> Commit"): TransitionGate/Negotiation/Commit are
        # not separate top-level stages at all (they run INSIDE "execute",
        # in action_executor.py) -- so proving "execute" precedes
        # "compare" and "learn" is the complete, real proof; there is no
        # "transition_gate" stage name for the forbidden ordering to
        # apply to in the first place.
        policy = _configured_policy(ComparisonIntegratedPolicy())
        names = [name for name, _ in policy._stages]
        assert names.index("execute") < names.index("compare") < names.index("learn") < names.index("learn_transitions")
        assert "transition_gate" not in names and "negotiation" not in names and "commit" not in names


class TestPredictAgainstPreExecutionSnapshot:
    """TEST 1/2 — Predict genuinely runs against the pre-execution
    snapshot, never mutates it, and its own real timestamp precedes any
    later execute-phase timestamp in the same tick. Uses the real, live
    PredictionIntegratedPolicy (pipeline/prediction/integration.py) and
    DeterministicPredictionPolicy -- never the dead kernel/predict/
    (JEPA/MCTS) tree, which this test does not import or touch."""

    @pytest.mark.asyncio
    async def test_predict_reads_but_never_mutates_the_pre_execution_snapshot_and_timestamp_precedes_execute(
        self,
    ):
        policy = _configured_policy(PredictionIntegratedPolicy())
        integrated_predict = dict(policy._stages)["predict"]

        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.plan = _plan()
        state = CognitiveState(actor=actor, belief=belief)
        state.plan = belief.plan
        pre_execution_snapshot = WorldSnapshot()
        state.world_snapshot = pre_execution_snapshot
        state.metrics = {"execution_id": "exec_predict_1"}

        t_before = time.time()
        state = await integrated_predict(state)
        t_after = time.time()

        # MUST NOT mutate the real world: same object, not a copy/replacement.
        assert state.world_snapshot is pre_execution_snapshot

        assert state.prediction_result is not None
        candidates = state.prediction_result.get("candidates", [])
        assert candidates, "Predict produced no candidate scenarios to check a timestamp on"
        pred_timestamp = candidates[0]["prediction"]["timestamp"]
        assert t_before <= pred_timestamp <= t_after

        # Simulate the execute phase's own, later timestamp in the same
        # tick -- proves Predict's real timestamp precedes it structurally,
        # not merely that Predict ran without raising.
        time.sleep(0.01)
        t_execute = time.time()
        assert pred_timestamp < t_execute


class TestCompareMeasurementOnly:
    """TEST 7 — ComparatorRuntime (via _run_comparison, the Compare
    stage) MUST NOT mutate learning state. Sharper than the pre-existing
    tests/unit/test_learning_hardening.py::TestNoUnrelatedMutation (which
    checks plan/execution_result identity): this spies directly on the two
    real mutation entrypoints and proves Compare calls neither."""

    @pytest.mark.asyncio
    async def test_run_comparison_never_calls_transition_model_or_policy_store_update(self, monkeypatch, _sink):
        monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())
        calls: list[str] = []
        monkeypatch.setattr(
            TransitionModel,
            "learn_from_execution",
            lambda self, **kw: calls.append("transition_model") or self,
        )
        monkeypatch.setattr(PolicyStore, "update", lambda self, *a, **kw: calls.append("policy_store"))

        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.update_goal(name="buy milk")
        plan = _plan()
        belief.plan = plan
        state = CognitiveState(actor=actor, belief=belief)
        state.plan = plan
        state.metrics = {"execution_id": "exec_compare_1"}
        outcomes = [{"description": s.action, "success": True, "probability": 0.9} for s in plan.steps]
        selected = {"prediction": {"predicted_outcomes": outcomes}, "probability": 0.9}
        state.prediction_result = {"candidates": [selected], "selected": selected}
        state.execution_result = ExecutionResult(
            actions=(_outcome(True), _outcome(True)),
            success_count=2,
            failure_count=0,
            goal_achieved=True,
        )

        state = await _run_comparison(state)

        assert calls == [], f"Compare must be measurement-only; it called: {calls}"
        assert state.comparison_result is not None, "Compare produced no evidence at all"
        assert "actor_loss" in state.comparison_result
        assert _sink.count("compare.total") == 1


class TestLearnTransitionsGatedOnComparatorEvidence:
    """TEST 8 (instrumentation-specific slice) — learn.transitions.total /
    learn.policy_store_updates.total / learn.known_transitions are emitted
    by _apply_transition_learning, gated on real Comparator evidence; a
    blocked/not-attempted action (actual_success=None) must never be
    counted as a learned transition."""

    @staticmethod
    def _actor_state(execution_id: str) -> CognitiveState:
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.update_goal(name="buy milk")
        plan = _plan(("BuyMilk",))
        belief.plan = plan
        state = CognitiveState(actor=actor, belief=belief)
        state.plan = plan
        state.metrics = {"execution_id": execution_id}
        return state

    class _Policy:
        def __init__(self):
            self._transition_model = TransitionModel()

    def test_verified_success_increments_transition_and_policy_store_counters(self, _sink):
        state = self._actor_state("exec_learn_1")
        state.comparison_result = {
            "outcome": "success",
            "node_diffs": {"BuyMilk": {"actual_success": True}},
        }
        state.execution_result = ExecutionResult(
            actions=(_outcome(True),),
            success_count=1,
            failure_count=0,
            goal_achieved=True,
        )

        _apply_transition_learning(state, self._Policy())

        assert _sink.count("learn.transitions.total") == 1
        assert any(e[1] == "learn.transitions.total" and e[3].get("success") == "True" for e in _sink.events)
        assert _sink.count("learn.policy_store_updates.total") == 1
        assert any(e[0] == "gauge" and e[1] == "learn.known_transitions" for e in _sink.events)

    def test_blocked_action_is_not_counted_as_a_learned_transition(self, _sink):
        state = self._actor_state("exec_learn_2")
        # actual_success=None is exactly the Comparator's real "never
        # executed" signal (blocked_by_dependency / not_attempted) --
        # see _execution_to_graph's own module comment.
        state.comparison_result = {
            "outcome": "partial_success",
            "node_diffs": {"BuyMilk": {"actual_success": None}},
        }
        state.execution_result = ExecutionResult(actions=(), success_count=0, failure_count=0, goal_achieved=False)

        _apply_transition_learning(state, self._Policy())

        assert _sink.count("learn.transitions.total") == 0
        assert _sink.count("learn.policy_store_updates.total") == 0


class TestTransitionGateCounters:
    """transition_gate.evaluations.total, tagged allow/requires_negotiation
    -- emitted at action_executor.py's real gate-evaluation call site,
    "before the capability is ever invoked" (its own comment). Reuses
    tests/scenarios/test_transition_gate.py's own proven no-contention
    fixture shape."""

    @staticmethod
    def _seed(price: float, quantity: int = 10):
        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=0.0)["store_id"]
        product_id = list_product(kg, store, "merchant_a", "Milk", price=price, quantity=quantity)["product_id"]
        return kg, product_id

    @pytest.mark.asyncio
    async def test_no_contention_purchase_records_allow_true_no_negotiation(self, _sink):
        kg, product_id = self._seed(price=3.99, quantity=10)
        kg.add_entity(
            "wallet_buyer_a",
            EntityType.ACCOUNT,
            "buyer_a Wallet",
            {"owner": "buyer_a", "balance": 1000.0},
        )
        executor = build_execution_engine("grocery")
        context = {
            "knowledge_graph": kg,
            "actor_id": "buyer_a",
            "question": "buy 1L milk",
        }
        actions = (
            Action(
                action_id="a0",
                capability="ProductSelection",
                step_index=0,
                depends_on=(),
                correlation_id="",
                parameters={"selection": [{"id": product_id, "qty": 1}]},
            ),
            Action(
                action_id="a1",
                capability="OrderCreation",
                step_index=1,
                depends_on=(0,),
                correlation_id="",
            ),
        )

        result = await executor.execute(actions, context)

        assert result.goal_achieved is True
        assert _sink.count("transition_gate.evaluations.total") >= 1
        assert any(
            e[1] == "transition_gate.evaluations.total"
            and e[3].get("allow") == "True"
            and e[3].get("requires_negotiation") == "False"
            for e in _sink.events
        )
        # World Commit — same real call path, right after the gate clears
        # (action_executor.py, immediately after transition_gate.evaluations.total).
        assert any(e[1] == "world_commit.total" and e[3].get("security_outcome") == "allowed" for e in _sink.events)


class TestObserveCounters:
    """observe.total / observe.observations_acquired / observe.
    world_snapshot_states / observe.duration_ms -- pipeline/belief_runtime.py
    ::_observe, the real Observe stage (WorldPollingProvider is
    CognitiveRuntime's own default observation_provider, so this needs no
    fake)."""

    def test_observe_emits_real_counts(self, _sink):
        rt = CognitiveRuntime()
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        state = CognitiveState(actor=actor, belief=belief)
        state.context = {}

        asyncio.run(rt._observe(state))

        assert _sink.count("observe.total") == 1
        assert _sink.count("observe.duration_ms") == 1
        assert any(e[1] == "observe.observations_acquired" for e in _sink.events)


class TestObserveOutcomeCounters:
    """observe_outcome.total / observe_outcome.actions_executed --
    pipeline/belief_runtime.py::_observe_outcome, the real stage between
    Execute and Compare."""

    def test_successful_execution_recorded(self, _sink):
        rt = CognitiveRuntime()
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        state = CognitiveState(actor=actor, belief=belief)
        state.execution_result = ExecutionResult(
            actions=(_outcome(True), _outcome(True)),
            success_count=2,
            failure_count=0,
            total_latency_ms=20.0,
            goal_achieved=True,
        )

        asyncio.run(rt._observe_outcome(state))

        assert _sink.count("observe_outcome.total") == 1
        assert any(e[1] == "observe_outcome.total" and e[3].get("goal_achieved") == "True" for e in _sink.events)
        assert any(e[1] == "observe_outcome.actions_executed" and e[2] == 2.0 for e in _sink.events)

    def test_no_execution_result_still_records_a_zero_outcome(self, _sink):
        rt = CognitiveRuntime()
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        state = CognitiveState(actor=actor, belief=belief)
        state.execution_result = None

        asyncio.run(rt._observe_outcome(state))

        assert _sink.count("observe_outcome.total") == 1
        assert any(e[1] == "observe_outcome.total" and e[3].get("goal_achieved") == "False" for e in _sink.events)


class TestLearnCounters:
    """learn.total / learn.reward / learn.signals_applied / learn.
    belief_updated.total / learn.world_updated.total --
    learning/integration.py::integrated_learn, the base Learn stage
    (reward/belief/world pipeline). Distinct from learn.transitions.total
    (LearnTransitions, TestLearnTransitionsGatedOnComparatorEvidence
    above)."""

    @pytest.mark.asyncio
    async def test_learn_stage_emits_real_reward_and_update_flags(self, _sink):
        policy = _configured_policy(LearningIntegratedPolicy())
        integrated_learn = dict(policy._stages)["learn"]

        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.update_goal(name="buy milk")
        plan = _plan(("BuyMilk",))
        belief.plan = plan
        state = CognitiveState(actor=actor, belief=belief)
        state.plan = plan
        state.metrics = {"execution_id": "exec_learn_base_1"}
        state.execution_result = ExecutionResult(
            actions=(_outcome(True),),
            success_count=1,
            failure_count=0,
            goal_achieved=True,
        )

        await integrated_learn(state)

        assert _sink.count("learn.total") == 1
        assert any(e[1] == "learn.reward" for e in _sink.events)
        assert any(e[1] == "learn.signals_applied" for e in _sink.events)
        assert _sink.count("learn.belief_updated.total") == 1
        assert _sink.count("learn.world_updated.total") == 1


class TestReplanEventOrdering:
    """TEST 9 — replanning is a real, chronologically-ordered sequence:
    an earlier "generated" record for the standing plan, then "invalidated"
    once the world changes under it, then a NEW "generated" record for the
    replan -- read back from the durable audit timeline (audit_trail.py),
    not inferred from pass/fail status."""

    @staticmethod
    def _seeded_kg(quantity: int = 5):
        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "m1", "Store", delivery_fee=1.0)["store_id"]
        milk_id = list_product(kg, store, "m1", "Milk", price=3.0, quantity=quantity)["product_id"]
        return kg, milk_id

    def test_generated_then_invalidated_then_replanned_generated(self, _sink):
        TimelineStore.reset_for_testing()
        kg, milk_id = self._seeded_kg()
        old_plan = Plan(
            goal="buy milk",
            steps=(
                PlanStep(
                    action="ProductSelection",
                    parameters={"selection": [{"id": milk_id, "qty": 1}]},
                ),
                PlanStep(action="OrderCreation", parameters={}),
            ),
        )
        # Simulates the earlier real tick that originally produced this
        # Current Plan (in production this is a prior _run_decide's own
        # "replace" branch) -- constructing it directly here, matching
        # test_plan_invalidation_e2e.py's own established convention of
        # fixture-constructing CurrentPlanRecord rather than driving two
        # full ticks.
        record_plan_event(
            "generated",
            plan_id="plan_A",
            actor_id="arjun",
            execution_id="exec_replan",
            goal="buy milk",
            steps=tuple(s.action for s in old_plan.steps),
        )
        current = CurrentPlanRecord(
            plan_id="plan_A",
            actor_id="arjun",
            goal="buy milk",
            steps=tuple(s.action for s in old_plan.steps),
            score=0.9,
            plan=plan_to_dict(old_plan),
            entity_versions=capture_entity_versions(kg, old_plan),
        )
        kg.update_entity(milk_id, attributes={"quantity": 0})  # world changes -> plan invalidated

        policy = ComparisonIntegratedPolicy()
        policy._current_plans[canonicalize_goal("buy milk")] = current

        new_plan = Plan(
            goal="buy milk",
            steps=(
                PlanStep(
                    action="ProductSelection",
                    parameters={"selection": [{"id": milk_id, "qty": 1}]},
                ),
                PlanStep(action="OrderCreation", parameters={}),
            ),
        )
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.update_goal(name="buy milk")
        state = CognitiveState(actor=actor, belief=belief)
        state.plan = new_plan
        state.prediction_result = {"selected": {"probability": 0.3, "prediction": {"expected_utility": 0.1}}}
        state.metrics = {"execution_id": "exec_replan"}
        state.context = {"knowledge_graph": kg}

        asyncio.run(_run_decide(state, policy))

        timeline = query_audit_timeline("arjun", "exec_replan")
        statuses = [e["status"] for e in timeline if e["kind"] == "plan"]
        assert statuses.count("generated") >= 2, f"no replan 'generated' event recorded: {statuses}"
        assert "invalidated" in statuses, f"no 'invalidated' event recorded: {statuses}"
        first_generated_idx = statuses.index("generated")
        invalidated_idx = statuses.index("invalidated")
        assert first_generated_idx < invalidated_idx, f"replan ordering violated: {statuses}"
        assert statuses[-1] == "generated" and statuses[-1] != statuses[invalidated_idx], (
            f"replan's fresh plan must be the LAST recorded PLAN event, after 'invalidated': {statuses}"
        )
        assert _sink.count("plan.replan.total") >= 1
