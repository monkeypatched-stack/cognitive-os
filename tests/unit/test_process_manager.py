"""Tests for the Process Manager (kernel/process/) — lifecycle state machine,
checkpoint/restore, compensation, human approval, idempotency.

Covers the core scenarios from the "Cognitive Process Manager" architecture
spec:
  1. normal execution to completion
  2. capability failure -> compensation -> ROLLED_BACK
  3. checkpoint -> restore (on a fresh ProcessManager instance) -> resume
  4. human approval gate: grant path
  5. human approval gate: reject path -> compensation
  6. invalid state transitions are rejected
  7. retry-exhaustion doesn't get stuck in WAITING forever
  8. idempotency key stability across retries of the same node
  9. undeclared capability compensation defaults to unrecoverable, not silent no-op

Written, not executed, per project convention (write test files; don't run
pytest as part of a fix/feature change) — each scenario here was independently
verified via standalone scripts during development.
"""

import asyncio
import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir
from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.execute.graph import (
    ExecutionGraph,
    GraphNode,
    GraphEdge,
    NodeState,
)
from src.monkey_brain.kernel.process.manager import (
    ProcessManager,
    InvalidTransitionError,
    ProcessNotFoundError,
)
from src.monkey_brain.kernel.process.models import RuntimeProcessState
from src.monkey_brain.kernel.process.compensation import (
    CompensationRegistry,
    CompensationSpec,
    CompensationType,
)
from src.monkey_brain.kernel.process.idempotency import (
    idempotency_key,
    IdempotencyClass,
    DEFAULT_IDEMPOTENCY_CLASS,
)


def _make_context(run_id: str, goal_type: str = "query") -> ExecutionContext:
    ir = build_intent_ir(
        intent={"intent": "test_intent", "confidence": 0.9},
        goal=type(
            "G",
            (),
            {
                "to_dict": lambda self: {"name": "test_goal", "goal_type": goal_type},
                "entities": (),
            },
        )(),
        run_id=run_id,
        question="test question",
    )
    return ExecutionContext.create(run_id=run_id, execution_mode=ExecutionMode.EXECUTE, intent_ir=ir, user_id="u1")


def _linear_graph(*capability_names: str) -> ExecutionGraph:
    graph = ExecutionGraph()
    prev = None
    for i, cap in enumerate(capability_names):
        node_id = f"n{i}"
        graph.add_node(GraphNode(id=node_id, type="step", label=node_id, props={"capability": cap}))
        if prev is not None:
            graph.add_edge(GraphEdge(src=prev, dst=node_id, rel="depends_on"))
        prev = node_id
    return graph


def test_normal_completion():
    async def scenario():
        pm = ProcessManager()
        run_id = "test-normal"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        assert rpcb.state == RuntimeProcessState.INITIALIZED

        await pm.start_process(run_id)
        for _ in range(5):
            await pm.tick_all()
            if pm.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        assert pm.get_process(run_id).state == RuntimeProcessState.COMPLETED

    asyncio.run(scenario())


def test_capability_failure_triggers_compensation():
    async def scenario():
        registry = CompensationRegistry()
        registry.register(
            "write_db",
            CompensationSpec(
                type=CompensationType.COMPENSATING_ACTION,
                compensating_capability="undo_write_db",
            ),
        )
        registry.register("flaky", CompensationSpec(type=CompensationType.NONE))

        class FakeBus:
            async def execute(self, name, args):
                class R:
                    def __init__(self, ok):
                        self.success = ok
                        self.output = {"name": name}
                        self.error = None if ok else "boom"

                return R(name != "flaky")

        async def exec_capability(name, inputs):
            return {"undone": name}

        pm = ProcessManager(
            compensation_registry=registry,
            execute_capability=exec_capability,
            max_repair_attempts=1,
        )
        run_id = "test-fail-compensate"
        ctx = _make_context(run_id, goal_type="update")
        graph = _linear_graph("write_db", "flaky")

        await pm.create_process(ctx, graph=graph, target="execute")
        pm._schedulers[run_id]._bus = FakeBus()
        await pm.start_process(run_id)

        for _ in range(10):
            await pm.tick_all()
            state = pm.get_process(run_id).state
            if state in (
                RuntimeProcessState.ROLLED_BACK,
                RuntimeProcessState.FAILED,
                RuntimeProcessState.COMPLETED,
            ):
                break

        final = pm.get_process(run_id)
        assert final.state == RuntimeProcessState.ROLLED_BACK
        assert len(final.compensation_log) == 1
        assert final.compensation_log[0].capability_name == "write_db"
        assert final.compensation_log[0].success

    asyncio.run(scenario())


def test_checkpoint_restore_resume_across_instances():
    async def scenario():
        pm = ProcessManager()
        run_id = "test-checkpoint"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b")

        await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)
        await pm.tick_all()  # only n0 runs
        assert graph.get_state("n0") == NodeState.COMPLETE
        assert graph.get_state("n1") == NodeState.PENDING

        await pm.suspend_process(run_id)
        ref = await pm.checkpoint_process(run_id)
        assert pm.get_process(run_id).state == RuntimeProcessState.CHECKPOINTED

        # A fresh ProcessManager sharing only the checkpoint store simulates
        # restoring on a different worker/instance.
        pm2 = ProcessManager(checkpoint_store=pm._checkpoint_store)
        restored = await pm2.restore_process(ref.checkpoint_id)
        assert restored.state == RuntimeProcessState.RESUMING
        assert restored.graph.get_state("n0") == NodeState.COMPLETE
        assert restored.graph.get_state("n1") == NodeState.PENDING

        await pm2.start_process(run_id)
        for _ in range(5):
            await pm2.tick_all()
            if pm2.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        final = pm2.get_process(run_id)
        assert final.state == RuntimeProcessState.COMPLETED
        assert final.graph.get_state("n1") == NodeState.COMPLETE

    asyncio.run(scenario())


def test_approval_gate_grant_path():
    async def scenario():
        events = []

        class FakeBus:
            async def publish(self, event_type, payload):
                events.append((event_type, payload))

        pm = ProcessManager(event_bus=FakeBus())
        run_id = "test-approve"
        ctx = _make_context(run_id, goal_type="update")
        graph = ExecutionGraph()
        graph.add_node(GraphNode(id="n0", type="step", label="n0", props={"capability": "cap_a"}))
        graph.add_node(
            GraphNode(
                id="gate",
                type="approval_gate",
                label="gate",
                props={"prompt": "confirm?"},
            )
        )
        graph.add_node(GraphNode(id="n1", type="step", label="n1", props={"capability": "cap_b"}))
        graph.add_edge(GraphEdge(src="n0", dst="gate", rel="depends_on"))
        graph.add_edge(GraphEdge(src="gate", dst="n1", rel="depends_on"))

        await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)
        for _ in range(5):
            await pm.tick_all()
            if pm.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        p = pm.get_process(run_id)
        assert p.state == RuntimeProcessState.WAITING
        assert p.approval_gate is not None and p.approval_gate.node_id == "gate"
        assert any(e[0] == "process.approval_requested" for e in events)

        await pm.approve(run_id, approver="alice", decision="approve", note="ok")
        assert pm.get_process(run_id).state == RuntimeProcessState.RUNNING
        assert graph.get_state("gate") == NodeState.COMPLETE

        for _ in range(5):
            await pm.tick_all()
            if pm.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        assert pm.get_process(run_id).state == RuntimeProcessState.COMPLETED
        assert any(e[0] == "process.approval_granted" for e in events)

    asyncio.run(scenario())


def test_approval_gate_reject_path_triggers_compensation():
    async def scenario():
        registry = CompensationRegistry()
        registry.register(
            "cap_a",
            CompensationSpec(
                type=CompensationType.COMPENSATING_ACTION,
                compensating_capability="undo_a",
            ),
        )

        async def exec_capability(name, inputs):
            return {"ok": True, "name": name}

        pm = ProcessManager(compensation_registry=registry, execute_capability=exec_capability)
        run_id = "test-reject"
        ctx = _make_context(run_id, goal_type="update")
        graph = ExecutionGraph()
        graph.add_node(GraphNode(id="n0", type="step", label="n0", props={"capability": "cap_a"}))
        graph.add_node(GraphNode(id="gate", type="approval_gate", label="gate", props={}))
        graph.add_edge(GraphEdge(src="n0", dst="gate", rel="depends_on"))

        await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)
        await pm.tick_all()
        await pm.tick_all()
        assert pm.get_process(run_id).state == RuntimeProcessState.WAITING

        await pm.approve(run_id, approver="bob", decision="reject", note="no")
        final = pm.get_process(run_id)
        assert final.state == RuntimeProcessState.ROLLED_BACK
        assert len(final.compensation_log) == 1
        assert final.compensation_log[0].capability_name == "cap_a"

    asyncio.run(scenario())


def test_invalid_transition_rejected():
    async def scenario():
        pm = ProcessManager()
        run_id = "test-invalid"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a")
        rpcb = await pm.create_process(ctx, graph=graph, target="execute")

        raised = False
        try:
            # INITIALIZED -> COMPLETED skips RUNNING entirely; must be rejected.
            await pm._transition(rpcb, RuntimeProcessState.COMPLETED)
        except InvalidTransitionError:
            raised = True
        assert raised

    asyncio.run(scenario())


def test_unknown_run_id_raises_process_not_found():
    async def scenario():
        pm = ProcessManager()
        raised = False
        try:
            await pm.start_process("does-not-exist")
        except ProcessNotFoundError:
            raised = True
        assert raised

    asyncio.run(scenario())


def test_retry_exhaustion_reaches_compensating_not_stuck_waiting():
    """Regression test for a bug found during development: transitioning to
    WAITING as soon as has_runnable_nodes() is False would permanently stop
    ticking (tick_all only advances RUNNING processes), which meant
    SelfHealingPolicy's retry-exhaustion — discovered incrementally, one
    on_failure() call per tick — could never actually complete. A node stuck
    FAILED with retry budget remaining must keep the process RUNNING (not
    WAITING) until that budget is confirmed exhausted.
    """

    async def scenario():
        registry = CompensationRegistry()
        registry.register("always_fails", CompensationSpec(type=CompensationType.NONE))

        class FailingBus:
            async def execute(self, name, args):
                class R:
                    success = False
                    output = {}
                    error = "always fails"

                return R()

        pm = ProcessManager(compensation_registry=registry, max_repair_attempts=1)
        run_id = "test-exhaustion"
        ctx = _make_context(run_id)
        graph = _linear_graph("always_fails")

        await pm.create_process(ctx, graph=graph, target="execute")
        pm._schedulers[run_id]._bus = FailingBus()
        await pm.start_process(run_id)

        seen_waiting_stuck = False
        for _ in range(10):
            await pm.tick_all()
            state = pm.get_process(run_id).state
            if state == RuntimeProcessState.WAITING:
                seen_waiting_stuck = True
            if state in (RuntimeProcessState.ROLLED_BACK, RuntimeProcessState.FAILED):
                break

        final = pm.get_process(run_id)
        assert final.state in (
            RuntimeProcessState.ROLLED_BACK,
            RuntimeProcessState.FAILED,
        )
        assert not seen_waiting_stuck, "process must not pass through WAITING while retry budget remains"

    asyncio.run(scenario())


def test_idempotency_key_stable_across_retries_of_same_node():
    key1 = idempotency_key("run-1", "node-a")
    key2 = idempotency_key("run-1", "node-a")
    assert key1 == key2

    key_other_node = idempotency_key("run-1", "node-b")
    assert key_other_node != key1

    assert DEFAULT_IDEMPOTENCY_CLASS == IdempotencyClass.NON_IDEMPOTENT


def test_undeclared_capability_compensation_is_not_silently_skipped():
    """An undeclared CompensationSpec must be treated as unrecoverable, never
    as an implicit NONE — silently skipping an unaudited capability's
    compensation would be worse than surfacing it as a failure.
    """

    async def scenario():
        from src.monkey_brain.kernel.process.compensation import (
            compensate,
            UNDECLARED_SPEC,
            CompensationType,
        )

        assert UNDECLARED_SPEC.type == CompensationType.IRREVERSIBLE
        assert UNDECLARED_SPEC.declared is False

        registry = CompensationRegistry()  # nothing registered

        graph = ExecutionGraph()
        graph.add_node(
            GraphNode(
                id="n0",
                type="step",
                label="n0",
                props={"capability": "mystery_capability"},
            )
        )
        graph.mark_running("n0")
        graph.mark_complete("n0", result={"ok": True})

        all_recovered, records = await compensate(graph, registry=registry, execute_capability=None)
        assert all_recovered is False
        assert len(records) == 1
        assert records[0].success is False

    asyncio.run(scenario())


if __name__ == "__main__":
    tests = [
        test_normal_completion,
        test_capability_failure_triggers_compensation,
        test_checkpoint_restore_resume_across_instances,
        test_approval_gate_grant_path,
        test_approval_gate_reject_path_triggers_compensation,
        test_invalid_transition_rejected,
        test_unknown_run_id_raises_process_not_found,
        test_retry_exhaustion_reaches_compensating_not_stuck_waiting,
        test_idempotency_key_stable_across_retries_of_same_node,
        test_undeclared_capability_compensation_is_not_silently_skipped,
    ]
    ok = 0
    failures = []
    for fn in tests:
        try:
            fn()
            ok += 1
        except Exception as e:
            failures.append(f"{fn.__name__}: {e!r}")
    print(f"ProcessManager: {ok}/{len(tests)} passed")
    for f in failures:
        print(f"  FAIL: {f}")
