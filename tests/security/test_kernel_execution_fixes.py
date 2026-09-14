"""Kernel/runtime review fixes — K-4 (capability authz fail-open) and K-5 (message bus).

NOTE: the earlier K-1/K-2/K-3 "findings" (scheduler↔bus mismatch) were WRONG. GraphScheduler
dispatches to a duck-typed bus documented in ProcessManager.create_process:
    async execute(capability_name, args) -> result with .success/.output/.error
which the real adapter (CodeGenRuntime's Broca bus, codegen_runtime.py:128) implements exactly.
The `no_bus` no-op path is likewise intentional (lifecycle tests/replay run without a dispatch
target). Those are not bugs and were reverted.
"""

from __future__ import annotations

import asyncio
import logging

from src.monkey_brain.kernel.execute.capabilities.bus import (
    CapabilityBus,
    CapabilityResult,
)


class Cap:
    """A capability as CapabilityBus expects it: .name, .fn, optional .required_permissions."""

    def __init__(self, name, fn, required_permissions=None):
        self.name = name
        self.fn = fn
        self.required_permissions = required_permissions or []


class Ctx:
    def __init__(self, permissions):
        self.permissions = permissions


# ── K-4: capability authorization must run even with NO context (fail closed) ────


def test_capability_requiring_permission_is_denied_without_context():
    """Authz was gated on `if context is not None`, so a caller that passed no context
    skipped the permission check entirely — authorization failed OPEN."""
    bus = CapabilityBus()
    bus.register_capability(Cap("secret", lambda kw: {"ok": True}, required_permissions=["perm-admin"]))

    res = asyncio.run(bus.execute("secret"))  # no context → holds no permissions
    assert res.success is False
    assert res.produced.get("error") == "authorization_denied"


def test_capability_with_permission_still_runs():
    bus = CapabilityBus()
    bus.register_capability(Cap("secret", lambda kw: {"ok": True}, required_permissions=["perm-admin"]))
    res = asyncio.run(bus.execute("secret", context=Ctx(["perm-admin"])))
    assert res.success is True and res.produced == {"ok": True}


def test_unrestricted_capability_still_runs_without_context():
    """Fail-closed must not break capabilities that declare no required permissions."""
    bus = CapabilityBus()
    bus.register_capability(Cap("open", lambda kw: {"answer": 42}))
    res = asyncio.run(bus.execute("open"))
    assert res.success is True and res.produced["answer"] == 42


# ── K-5: message bus must hold task refs, surface async errors, never silently drop ──


def test_async_subscriber_runs_and_task_ref_is_held():
    from src.monkey_brain.runtime.message_bus import MessageBus, Message

    bus = MessageBus()
    seen = []

    async def sub(msg):
        seen.append(msg)

    bus.subscribe("evt", sub)

    async def scenario():
        bus.publish(Message(message_type="evt", payload={}))
        assert bus._tasks, "no strong reference held — asyncio can GC the task mid-flight"
        await asyncio.gather(*list(bus._tasks))

    asyncio.run(scenario())
    assert len(seen) == 1


def test_async_subscriber_exception_is_surfaced(caplog):
    from src.monkey_brain.runtime.message_bus import MessageBus, Message

    bus = MessageBus()

    async def boom(msg):
        raise RuntimeError("subscriber exploded")

    bus.subscribe("evt", boom)

    async def scenario():
        bus.publish(Message(message_type="evt", payload={}))
        await asyncio.gather(*list(bus._tasks), return_exceptions=True)

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert any("Async subscriber failed" in r.getMessage() for r in caplog.records)


def test_publish_with_no_running_loop_is_loud_not_silent(caplog):
    """publish() is sync; with no loop, create_task raised RuntimeError which was swallowed
    at DEBUG — the coroutine was never scheduled and the message was silently lost."""
    from src.monkey_brain.runtime.message_bus import MessageBus, Message

    bus = MessageBus()

    async def sub(msg):
        pass

    bus.subscribe("evt", sub)
    with caplog.at_level(logging.ERROR):
        bus.publish(Message(message_type="evt", payload={}))
    assert any("no running event loop" in r.getMessage() for r in caplog.records)


# ── K-6: a hung capability must not hang the workload forever ────────────────────


def test_hung_capability_is_timed_out_not_hung():
    """The scheduler awaited bus.execute() with no bound: one hung handler (LLM/network)
    blocked the tick — and the whole workload — indefinitely."""
    import time as _time
    from src.monkey_brain.kernel.execute.graph import (
        ExecutionGraph,
        GraphNode,
        NodeState,
    )
    from src.monkey_brain.kernel.fix.scheduler.graph_scheduler import GraphScheduler

    class HangingBus:
        async def execute(self, capability_name, args=None):  # duck-typed contract
            await asyncio.sleep(60)  # never returns in time

    g = ExecutionGraph(id="g")
    g.add_node(GraphNode(id="n1", label="slow", type="step", props={"capability": "slow"}))

    t0 = _time.monotonic()
    asyncio.run(GraphScheduler(bus=HangingBus(), step_timeout=0.3).run(g))
    elapsed = _time.monotonic() - t0

    assert elapsed < 10, "capability was not bounded — the workload hung"
    assert g.get_state("n1") == NodeState.FAILED


def test_normal_capability_still_completes_through_the_duck_typed_bus():
    """Guard the contract I previously broke: positional args, .success/.output/.error."""
    from src.monkey_brain.kernel.execute.graph import (
        ExecutionGraph,
        GraphNode,
        NodeState,
    )
    from src.monkey_brain.kernel.fix.scheduler.graph_scheduler import GraphScheduler

    class Res:
        success, output, error = True, {"answer": 42}, ""

    class OkBus:
        def __init__(self):
            self.calls = []

        async def execute(self, capability_name, args=None):
            self.calls.append((capability_name, args))
            return Res()

    bus = OkBus()
    g = ExecutionGraph(id="g")
    g.add_node(GraphNode(id="n1", label="ok", type="step", props={"capability": "ok"}))
    asyncio.run(GraphScheduler(bus=bus).run(g))

    # step_bus_args (pipeline/graph_execution.py) now builds a richer
    # {action, parameters, context} dict instead of bare kwargs -- still
    # positional (capability_name, args), which is the actual contract
    # this test guards.
    assert bus.calls == [("ok", {"action": "ok", "parameters": {}, "context": {}})]
    assert g.get_state("n1") == NodeState.COMPLETE


# ── K-7: unbounded growth ────────────────────────────────────────────────────────


def test_compliance_results_are_bounded_but_totals_stay_accurate():
    from src.monkey_brain.kernel.compliance import RegulatoryComplianceEngine

    eng = RegulatoryComplianceEngine(max_results=100)
    for _ in range(200):
        eng.check("read_data", {"purpose": "x"})
    assert len(eng._results) <= 100  # retention bounded
    s = eng.summary()
    assert s["total_checks"] > 100  # lifetime total NOT truncated
    assert s["retained_results"] <= 100


def test_audit_log_bounds_memory_but_query_still_serves_old_events(tmp_path):
    """Eviction is safe only because events are durable: an audit query for evicted
    revisions must fall back to the log file, never silently return a truncated result.
    """
    from src.monkey_brain.kernel.storage import AppendOnlyLog

    log = AppendOnlyLog(path=str(tmp_path), max_in_memory=50)
    for i in range(200):
        log.append("acme", "audit", {"i": i})

    assert len(log._events["acme"]) <= 50  # memory bounded
    assert log._evicted["acme"] > 0
    assert log.latest_revision("acme") == 200

    all_events = log.query("acme", after_revision=0, limit=1000)
    assert len(all_events) == 200  # served from disk — nothing lost
    assert all_events[0].revision == 1


# ── K-8: governance escalation must not be silently dropped ──────────────────────


def test_escalation_task_reference_is_held_and_failures_surface(caplog):
    from src.monkey_brain.kernel.fix.policy import consensus

    async def scenario():
        async def boom():
            raise RuntimeError("cingulate down")

        loop = asyncio.get_running_loop()
        task = loop.create_task(boom())
        consensus._ESCALATION_TASKS.add(task)
        task.add_done_callback(consensus._ESCALATION_TASKS.discard)
        task.add_done_callback(consensus._log_escalation_failure)
        await asyncio.gather(task, return_exceptions=True)

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert any("Cingulate escalation failed" in r.getMessage() for r in caplog.records)
