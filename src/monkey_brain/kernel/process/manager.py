"""ProcessManager — Kernel-level subsystem owning the lifecycle of Runtime
Processes (one compile->execute lifecycle of a Workload's ExecutionGraph).

Purely additive: does not modify CognitiveRuntime/SimulationRuntime/
ComparatorRuntime, GoalExecutor, SelfHealingPolicy, or run_store.py. It wraps
GraphScheduler by ticking it incrementally (run_one_tick(), added alongside
this package) instead of run-to-completion, so it can observe and act on
process-level state between rounds — suspend, checkpoint, wait for approval,
compensate on exhausted retries.

Process-level lifecycle (RuntimeProcessState) is layered ABOVE ExecutionGraph's
existing per-node NodeState, not a replacement for it:

    CREATED -> INITIALIZED -> RUNNING -> {WAITING, SUSPENDED, COMPLETED,
               COMPENSATING, FAILED, TERMINATED}
    WAITING -> {RUNNING, SUSPENDED, TERMINATED}
    SUSPENDED -> {CHECKPOINTED, RUNNING, TERMINATED}
    CHECKPOINTED -> {RESUMING, TERMINATED}
    RESUMING -> {RUNNING, FAILED}
    COMPENSATING -> {ROLLED_BACK, FAILED}
    {ROLLED_BACK, COMPLETED, FAILED, TERMINATED} are terminal.

See VALID_TRANSITIONS in models.py for the enforced table, and the
"Architecture Sprint: Design the Cognitive Process Manager" spec this
implements for full justification of each design choice.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Literal

from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.graph import ExecutionGraph, NodeState
from src.monkey_brain.kernel.fix.scheduler.graph_scheduler import GraphScheduler
from src.monkey_brain.kernel.fix.self_healing.workload import SelfHealingPolicy
from src.monkey_brain.kernel.process.checkpoint import (
    CheckpointOpsMixin,
    CheckpointStore,
    build_checkpoint,
)
from src.monkey_brain.kernel.process.compensation import (
    CompensationRegistry,
    get_compensation_registry,
)
from src.monkey_brain.kernel.process.compensation import compensate as run_compensation
from src.monkey_brain.kernel.process.expansion_policy import _TrackingExpansionPolicy
from src.monkey_brain.kernel.process.models import (
    VALID_TRANSITIONS,
    ApprovalGateState,
    CheckpointRef,
    InvalidTransitionError,
    ProcessError,
    ProcessNotFoundError,
    ProcessResourceLimits,
    RuntimeProcessControlBlock,
    RuntimeProcessState,
)

logger = logging.getLogger("agentos.process.manager")


class ProcessManager(CheckpointOpsMixin):
    """Owns the Process Table and the macro-scheduler for Runtime Processes.

    One instance per Kernel (see kernel/kernel.py's Kernel.boot()).
    """

    def __init__(
        self,
        *,
        event_bus: Any = None,
        checkpoint_store: CheckpointStore | None = None,
        compensation_registry: CompensationRegistry | None = None,
        execute_capability: Callable[[str, dict[str, Any]], Any] | None = None,
        max_repair_attempts: int = 3,
        max_scheduler_iterations_per_tick: int = 1,
        graph_store: Any = None,
    ) -> None:
        self._event_bus = event_bus
        self._checkpoint_store = checkpoint_store or CheckpointStore()
        self._compensation_registry = compensation_registry or get_compensation_registry()
        self._execute_capability = execute_capability
        self._max_repair_attempts = max_repair_attempts
        self._max_scheduler_iterations_per_tick = max(1, max_scheduler_iterations_per_tick)
        self._graph_store = graph_store

        self._table: dict[str, RuntimeProcessControlBlock] = {}
        self._schedulers: dict[str, GraphScheduler] = {}
        self._expansion_policies: dict[str, _TrackingExpansionPolicy] = {}
        self._last_graph_sync: dict[str, dict[str, str]] = {}  # run_id -> {node_id: state}

    # ------------------------------------------------------------------
    # Internal: transitions + events
    # ------------------------------------------------------------------

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)

    async def _transition(
        self,
        rpcb: RuntimeProcessControlBlock,
        target: RuntimeProcessState,
        **event_extra: Any,
    ) -> None:
        current = rpcb.state
        if target not in VALID_TRANSITIONS.get(current, frozenset()):
            raise InvalidTransitionError(rpcb.run_id, current, target)
        rpcb.state = target
        await self._publish(f"process.{target.value}", {"run_id": rpcb.run_id, **event_extra})
        logger.info("run=%r transition %s -> %s", rpcb.run_id, current.value, target.value)

    def _require(self, run_id: str) -> RuntimeProcessControlBlock:
        rpcb = self._table.get(run_id)
        if rpcb is None:
            raise ProcessNotFoundError(run_id)
        return rpcb

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create_process(
        self,
        context: ExecutionContext,
        *,
        workload: Any = None,
        graph: ExecutionGraph | None = None,
        target: Literal["execute", "simulate", "compare", "codegen"] = "execute",
        priority: int = 0,
        parent_run_id: str | None = None,
        resource_limits: ProcessResourceLimits | None = None,
        timeout_seconds: float | None = None,
        capability_bus: Any = None,
        execution_mode: Literal["serial", "parallel"] = "serial",
    ) -> RuntimeProcessControlBlock:
        """Allocate a new Runtime Process. Exactly one of `workload`/`graph`
        must be given — a pre-built ExecutionGraph (for callers that already
        have one, e.g. replay or tests) or a Workload that already carries a
        planner-built graph on `workload.graph`.

        `capability_bus`: the object GraphScheduler dispatches step nodes to
        (anything with `async execute(capability_name, args) -> result-with-
        .success/.output/.error`). Defaults to None (GraphScheduler's own
        "no_bus" no-op path) — runtimes with their own dispatch target (e.g.
        CodeGenRuntime's Broca-agent adapter) pass one in here, or via
        set_capability_bus() afterward.

        `execution_mode`: "serial" (default) runs simultaneously-runnable
        nodes one at a time; "parallel" fires them concurrently via
        asyncio.gather(). Default is "serial" because many capabilities
        route to a local Ollama backend, which does not handle concurrent
        generation requests well — only opt into "parallel" when every
        capability the graph might reach tolerates concurrency.
        """
        if (workload is None) == (graph is None):
            raise ValueError("create_process requires exactly one of workload= or graph=")

        run_id = context.run_id
        if run_id in self._table:
            raise ValueError(f"run_id {run_id!r} already has a process")

        graph_source = graph if graph is not None else getattr(workload, "graph", None)
        if graph_source is None:
            raise ValueError(
                "create_process requires a pre-built graph; the planner must construct it before runtime handoff"
            )

        rpcb = RuntimeProcessControlBlock(
            run_id=run_id,
            context=context,
            graph=graph_source,
            target=target,
            priority=priority,
            parent_run_id=parent_run_id,
            resource_limits=resource_limits,
            timeout_at=(time.time() + timeout_seconds) if timeout_seconds else None,
        )
        self._table[run_id] = rpcb
        await self._publish("process.created", {"run_id": run_id, "target": target})

        policy = _TrackingExpansionPolicy(SelfHealingPolicy(max_repair_attempts=self._max_repair_attempts))
        self._expansion_policies[run_id] = policy
        self._schedulers[run_id] = GraphScheduler(
            bus=capability_bus, expansion_policy=policy, execution_mode=execution_mode
        )

        await self._transition(rpcb, RuntimeProcessState.INITIALIZED)
        return rpcb

    def set_capability_bus(self, run_id: str, bus: Any) -> None:
        """Swap the capability dispatch target for an existing process — e.g.
        after restore_process(), where the bus itself (unlike graph/context)
        is never part of the checkpoint since it typically holds live
        connections/registries that don't serialize.
        """
        scheduler = self._schedulers.get(run_id)
        if scheduler is None:
            raise ProcessNotFoundError(run_id)
        scheduler._bus = bus

    async def start_process(self, run_id: str) -> None:
        rpcb = self._require(run_id)
        await self._transition(rpcb, RuntimeProcessState.RUNNING)

    async def suspend_process(self, run_id: str) -> None:
        rpcb = self._require(run_id)
        await self._transition(rpcb, RuntimeProcessState.SUSPENDED)

    async def resume_process(self, run_id: str) -> None:
        """Resume from SUSPENDED (in-memory, cheap) directly back to RUNNING.
        Resuming from a CHECKPOINTED process must go through restore_process()
        instead, which reconstructs the RPCB from durable storage.
        """
        rpcb = self._require(run_id)
        await self._transition(rpcb, RuntimeProcessState.RUNNING)

    async def terminate_process(self, run_id: str, reason: str = "operator") -> None:
        rpcb = self._require(run_id)
        await self._transition(rpcb, RuntimeProcessState.TERMINATED, reason=reason)

    # Checkpointing: checkpoint_process()/restore_process() live in
    # CheckpointOpsMixin (checkpoint.py) — split out purely to keep this
    # file under the architecture validator's 500-line threshold.

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    async def compensate_process(self, run_id: str) -> None:
        rpcb = self._require(run_id)
        if rpcb.state != RuntimeProcessState.COMPENSATING:
            await self._transition(rpcb, RuntimeProcessState.COMPENSATING)

        all_recovered, records = await run_compensation(
            rpcb.graph,
            registry=self._compensation_registry,
            execute_capability=self._execute_capability,
        )
        for r in records:
            rpcb.compensation_log.append(r)
            await self._publish(
                "process.compensated_node",
                {
                    "run_id": run_id,
                    "node_id": r.node_id,
                    "capability": r.capability_name,
                    "success": r.success,
                },
            )

        if all_recovered:
            await self._transition(rpcb, RuntimeProcessState.ROLLED_BACK)
        else:
            rpcb.error = ProcessError(message="one or more nodes could not be compensated")
            await self._transition(rpcb, RuntimeProcessState.FAILED)

    async def retry_node(self, run_id: str, node_id: str) -> None:
        """Manual override — resets a FAILED node to PENDING outside
        SelfHealingPolicy's automatic budget, for operator-initiated retry
        after e.g. fixing an external system the capability depends on.
        """
        rpcb = self._require(run_id)
        if rpcb.graph.get_state(node_id) != NodeState.FAILED:
            raise ValueError(f"node {node_id!r} is not FAILED (state={rpcb.graph.get_state(node_id).value})")
        rpcb.graph.set_state(node_id, NodeState.PENDING)
        self._expansion_policies[run_id].exhausted.discard(node_id)

    # ------------------------------------------------------------------
    # Human approval
    # ------------------------------------------------------------------

    async def approve(
        self,
        run_id: str,
        approver: str,
        decision: Literal["approve", "reject"],
        note: str = "",
    ) -> None:
        rpcb = self._require(run_id)
        gate = rpcb.approval_gate
        if gate is None:
            raise ValueError(f"run={run_id!r} has no pending approval gate")

        gate.approver = approver
        gate.decision = decision
        gate.note = note
        gate.decided_at = time.time()

        if decision == "approve":
            rpcb.graph.mark_complete(gate.node_id, result={"approved_by": approver, "note": note})
            await self._publish(
                "process.approval_granted",
                {"run_id": run_id, "node_id": gate.node_id, "approver": approver},
            )
            rpcb.approval_gate = None
            await self._transition(rpcb, RuntimeProcessState.RUNNING)
        else:
            rpcb.graph.mark_failed(gate.node_id, error=f"rejected by {approver}: {note}")
            await self._publish(
                "process.approval_rejected",
                {
                    "run_id": run_id,
                    "node_id": gate.node_id,
                    "approver": approver,
                    "note": note,
                },
            )
            rpcb.approval_gate = None
            await self._transition(rpcb, RuntimeProcessState.COMPENSATING)
            await self.compensate_process(run_id)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    async def replay_process(
        self,
        run_id: str,
        *,
        target: Literal["execute", "simulate", "compare", "codegen"] | None = None,
        mongo_client: Any = None,
    ) -> Any:
        """Reuses the existing target-based dispatch in run_store.replay() —
        replay is not a new execution mode, it's the existing three-runtime
        dispatch applied to a stored IntentIR. `target=None` replays through
        whichever runtime originally compiled it (real replay); passing
        target="simulate" explicitly is the dry-run path (substitutes the
        world-model simulator for real capabilities).
        """
        from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
        from src.monkey_brain.kernel.plan.goals.run_store import replay as _replay

        store = get_run_store()
        original_target = store.get_target(run_id)
        if original_target is None:
            raise ValueError(f"no stored run found for run_id={run_id!r}")

        if target is not None and target != original_target:
            ir = store.get(run_id)
            store.store(run_id, ir, target=target)

        return await _replay(run_id, mongo_client)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_process(self, run_id: str) -> RuntimeProcessControlBlock | None:
        return self._table.get(run_id)

    def list_processes(self, state: RuntimeProcessState | None = None) -> list[RuntimeProcessControlBlock]:
        procs = list(self._table.values())
        if state is not None:
            procs = [p for p in procs if p.state == state]
        return procs

    async def shutdown(self, app: Any = None) -> None:
        """Called from Kernel.shutdown(). Does not try to force-complete or
        checkpoint in-flight processes on its own initiative — that's a
        policy decision (checkpoint everything? abandon? wait?) that belongs
        to whoever operates the deployment, not something to guess at here.
        What it does do: make any still-RUNNING/WAITING process visible in
        the shutdown log instead of the process table silently vanishing
        with the interpreter, which is what happened before this existed.
        """
        unfinished = [
            rpcb
            for rpcb in self._table.values()
            if rpcb.state
            not in (
                RuntimeProcessState.COMPLETED,
                RuntimeProcessState.FAILED,
                RuntimeProcessState.ROLLED_BACK,
                RuntimeProcessState.TERMINATED,
            )
        ]
        if unfinished:
            logger.warning(
                "ProcessManager shutdown: %d process(es) not in a terminal state: %s",
                len(unfinished),
                [(r.run_id, r.state.value) for r in unfinished],
            )
        await self._publish("process_manager.shutdown", {"unfinished": len(unfinished)})

    # ------------------------------------------------------------------
    # Macro-scheduler — advances every RUNNING process one tick each
    # ------------------------------------------------------------------

    async def tick_all(self) -> None:
        """Advance every RUNNING process by exactly one GraphScheduler tick,
        highest RPCB.priority first. Intended to be called repeatedly by an
        external driver loop (e.g. an asyncio task the Kernel starts) — this
        method itself does not loop or sleep, so callers control the cadence
        and can stop cleanly.

        Priority only controls tick *order* within one pass, not whether a
        lower-priority process gets ticked at all — every RUNNING process
        still gets exactly one tick per call. That's a deliberate scope
        limit: real starvation prevention (skipping low-priority processes
        under load) needs a resource/concurrency budget this method doesn't
        have yet (see ProcessResourceLimits, currently unused by tick_all).
        """
        running = [r for r in self._table.values() if r.state == RuntimeProcessState.RUNNING]
        running.sort(key=lambda r: r.priority, reverse=True)
        for rpcb in running:
            await self._tick_one(rpcb)

    async def _tick_one(self, rpcb: RuntimeProcessControlBlock) -> None:
        if rpcb.timeout_at is not None and time.time() > rpcb.timeout_at:
            rpcb.error = ProcessError(message="timeout exceeded")
            await self._transition(rpcb, RuntimeProcessState.COMPENSATING, reason="timeout")
            await self.compensate_process(rpcb.run_id)
            return

        state_before = {n.id: rpcb.graph.get_state(n.id) for n in rpcb.graph.all_nodes()}

        scheduler = self._schedulers[rpcb.run_id]
        # Bounded by max_scheduler_iterations_per_tick (default 1 — identical
        # to always calling run_one_tick() exactly once, as before this loop
        # existed). >1 lets one external tick_all() pass drive a chain of
        # immediately-ready nodes through several scheduler rounds without
        # waiting for another driver call, while still returning control
        # (and letting tick_all() reach other RUNNING processes) once the
        # bound is hit rather than running this one process to completion.
        # Stops early once a round executes nothing — further rounds would
        # be identical no-ops until something external unblocks a node.
        # Same "run rounds until nothing executes" idiom GraphScheduler.run()
        # already implements internally, just bounded by a count here
        # instead of run()'s own has_runnable_nodes() check, so this stays
        # in step if that termination condition ever changes.
        for _ in range(self._max_scheduler_iterations_per_tick):
            executed = await scheduler.run_one_tick(rpcb.graph, iteration=rpcb.graph.iteration)
            if not executed:
                break
        await self._recompute_process_state(rpcb)

        state_after = {n.id: rpcb.graph.get_state(n.id) for n in rpcb.graph.all_nodes()}
        changed_nodes = [node_id for node_id, s_before in state_before.items() if state_after.get(node_id) != s_before]

        if changed_nodes:
            await self._sync_graph_state(rpcb, changed_nodes, state_after)
            await self._auto_checkpoint(rpcb)

    async def _recompute_process_state(self, rpcb: RuntimeProcessControlBlock) -> None:
        graph = rpcb.graph
        policy = self._expansion_policies[rpcb.run_id]
        rpcb.retry_budget = dict(policy.attempts)

        all_nodes = graph.all_nodes()
        states = {n.id: graph.get_state(n.id) for n in all_nodes}

        failed_exhausted = [
            node_id for node_id, state in states.items() if state == NodeState.FAILED and node_id in policy.exhausted
        ]
        if failed_exhausted:
            rpcb.error = ProcessError(
                message=f"node(s) failed, retries exhausted: {failed_exhausted}",
                node_id=failed_exhausted[0],
            )
            await self._transition(rpcb, RuntimeProcessState.COMPENSATING)
            await self.compensate_process(rpcb.run_id)
            return

        # An approval_gate left RUNNING (see GraphScheduler._execute_node) is
        # the only node type the scheduler deliberately never completes on
        # its own — its presence means the process is blocked pending a
        # human decision.
        pending_gates = [n for n in graph.nodes_by_type("approval_gate") if states[n.id] == NodeState.RUNNING]
        if pending_gates:
            gate_node = pending_gates[0]
            if rpcb.approval_gate is None:
                rpcb.approval_gate = ApprovalGateState(
                    node_id=gate_node.id,
                    prompt=str(gate_node.props.get("prompt", "")),
                )
                await self._publish(
                    "process.approval_requested",
                    {
                        "run_id": rpcb.run_id,
                        "node_id": gate_node.id,
                        "prompt": rpcb.approval_gate.prompt,
                    },
                )
            await self._transition(rpcb, RuntimeProcessState.WAITING, reason="approval_gate")
            return

        all_terminal = all(s in (NodeState.COMPLETE, NodeState.SKIPPED, NodeState.FAILED) for s in states.values())
        any_failed = any(s == NodeState.FAILED for s in states.values())

        if all_terminal and not any_failed:
            await self._transition(rpcb, RuntimeProcessState.COMPLETED)
            return

        # A FAILED step node whose retry budget isn't exhausted yet will
        # never itself become runnable again (its own state stays FAILED
        # forever — SelfHealingPolicy retries by injecting a NEW node, it
        # doesn't resurrect the old one), so has_runnable_nodes() can be
        # False even though the process isn't actually stuck: on_failure()
        # only advances one call per tick, and tick_all() only ticks
        # RUNNING processes — transitioning to WAITING here would freeze
        # retry-exhaustion tracking forever. Stay RUNNING until that
        # specific node's budget is confirmed exhausted (handled above) or
        # something becomes runnable again.
        unexhausted_failed = [
            node_id
            for node_id, state in states.items()
            if state == NodeState.FAILED
            and node_id in {n.id for n in graph.get_step_nodes()}
            and node_id not in policy.exhausted
        ]
        if not graph.has_runnable_nodes() and not all_terminal and not unexhausted_failed:
            # Blocked on something external (async capability, or — since
            # this can't be distinguished cheaply from a true dependency
            # deadlock — a graph authoring error). Flagged as a known
            # limitation in the design spec rather than guessed at.
            await self._transition(rpcb, RuntimeProcessState.WAITING, reason="no_runnable_nodes")
            return

        # Still has runnable or in-flight work — stays RUNNING (no-op transition).

    # ------------------------------------------------------------------
    # Live graph sync + auto-checkpoint
    # ------------------------------------------------------------------

    async def _sync_graph_state(
        self,
        rpcb: RuntimeProcessControlBlock,
        changed_nodes: list[str],
        states: dict[str, Any],
    ) -> None:
        """Sync changed node states to Neo4j (live, not snapshot)."""
        if self._graph_store is None:
            return
        try:
            for node_id in changed_nodes:
                state_val = states.get(node_id, "unknown")
                state_str = state_val.value if hasattr(state_val, "value") else str(state_val)
                await self._graph_store.sync_node_state(
                    node_id,
                    state_str,
                    run_id=rpcb.run_id,
                )
        except Exception as exc:
            logger.debug("live graph sync failed (non-fatal): %s", exc)

    async def _auto_checkpoint(self, rpcb: RuntimeProcessControlBlock) -> None:
        """Persist a checkpoint after any state-changing tick (durable, not snapshot).

        Only checkpoints processes that are still alive (not terminal) to
        avoid checkpointing processes that are about to be cleaned up.
        """
        if rpcb.state in (
            RuntimeProcessState.COMPLETED,
            RuntimeProcessState.FAILED,
            RuntimeProcessState.ROLLED_BACK,
            RuntimeProcessState.TERMINATED,
        ):
            return
        try:
            checkpoint_id = f"ckpt-{rpcb.run_id}-{len(rpcb.checkpoints)}"[:80]
            ckpt = await build_checkpoint(rpcb, checkpoint_id)
            save_result = self._checkpoint_store.save(ckpt)
            if asyncio.iscoroutine(save_result):
                await save_result
            ref = CheckpointRef(checkpoint_id=checkpoint_id, run_id=rpcb.run_id)
            rpcb.checkpoints.append(ref)
        except Exception as exc:
            logger.debug("auto-checkpoint failed (non-fatal): %s", exc)
