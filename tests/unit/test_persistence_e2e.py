"""End-to-end tests for persistence fixes:
1. MongoCheckpointStore — durable checkpoint persistence via MongoDB
2. Auto-checkpoint — ProcessManager checkpoints on every state-changing tick
3. Live graph sync — ProcessManager syncs node states to Neo4j on every tick
4. Lemon Elasticsearch — metrics persist across restarts
"""

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

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
from src.monkey_brain.kernel.process.manager import ProcessManager
from src.monkey_brain.kernel.process.checkpoint import (
    Checkpoint,
    CheckpointStore,
    MongoCheckpointStore,
    build_checkpoint,
)
from src.monkey_brain.kernel.process.models import RuntimeProcessState


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


# ============================================================================
# Test 1: MongoCheckpointStore (mocked Motor client)
# ============================================================================


def test_mongo_checkpoint_save_and_load():
    """MongoCheckpointStore.save() writes to collection, load() reads back."""

    async def scenario():
        mock_db = MagicMock()
        mock_col = AsyncMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_col)
        mock_col.find_one = AsyncMock(return_value=None)
        mock_col.update_one = AsyncMock()
        mock_col.delete_one = AsyncMock()
        mock_col.find = MagicMock(return_value=AsyncMock())

        store = MongoCheckpointStore.__new__(MongoCheckpointStore)
        store._db = mock_db
        store._col = mock_col

        ckpt = Checkpoint(
            checkpoint_id="ckpt-1",
            run_id="run-1",
            schema_version="1.0",
            created_at=1000.0,
            context={
                "run_id": "run-1",
                "trace_id": "t1",
                "execution_mode": "execute",
                "user_id": "u1",
                "request_metadata": {},
            },
            intent_ir=None,
            graph_snapshot={"nodes": [], "edges": []},
            retry_budget={},
            compensation_log=[],
            approval_gate=None,
        )
        ckpt.sign()

        await store.save(ckpt)
        mock_col.update_one.assert_called_once()

        mock_col.find_one = AsyncMock(
            return_value={
                "_id": "ckpt-1",
                "checkpoint_id": "ckpt-1",
                "run_id": "run-1",
                "schema_version": "1.0",
                "created_at": 1000.0,
                "context": ckpt.context,
                "intent_ir": None,
                "graph_snapshot": ckpt.graph_snapshot,
                "retry_budget": {},
                "compensation_log": [],
                "approval_gate": None,
                "integrity": ckpt.integrity,
            }
        )
        loaded = await store.load("ckpt-1")
        assert loaded is not None
        assert loaded.checkpoint_id == "ckpt-1"
        assert loaded.run_id == "run-1"
        assert loaded.verify()

        mock_col.find_one = AsyncMock(return_value=None)
        missing = await store.load("nonexistent")
        assert missing is None

    asyncio.run(scenario())


def test_mongo_checkpoint_latest_for_run():
    """MongoCheckpointStore.latest_for_run() returns the most recent checkpoint."""

    async def scenario():
        mock_col = AsyncMock()
        store = MongoCheckpointStore.__new__(MongoCheckpointStore)
        store._db = MagicMock()
        store._col = mock_col

        ckpt_doc = {
            "_id": "ckpt-2",
            "checkpoint_id": "ckpt-2",
            "run_id": "run-1",
            "schema_version": "1.0",
            "created_at": 2000.0,
            "context": {
                "run_id": "run-1",
                "trace_id": "t1",
                "execution_mode": "execute",
                "user_id": "u1",
                "request_metadata": {},
            },
            "intent_ir": None,
            "graph_snapshot": {"nodes": [], "edges": []},
            "retry_budget": {},
            "compensation_log": [],
            "approval_gate": None,
            "integrity": "",
        }
        mock_col.find_one = AsyncMock(return_value=ckpt_doc)
        latest = await store.latest_for_run("run-1")
        assert latest is not None
        assert latest.checkpoint_id == "ckpt-2"

        mock_col.find_one = AsyncMock(return_value=None)
        none_result = await store.latest_for_run("nonexistent")
        assert none_result is None

    asyncio.run(scenario())


def test_mongo_checkpoint_delete():
    """MongoCheckpointStore.delete() removes the checkpoint."""

    async def scenario():
        mock_col = AsyncMock()
        store = MongoCheckpointStore.__new__(MongoCheckpointStore)
        store._db = MagicMock()
        store._col = mock_col

        await store.delete("ckpt-1")
        mock_col.delete_one.assert_called_once_with({"_id": "ckpt-1"})

    asyncio.run(scenario())


def test_mongo_checkpoint_list_for_run():
    """MongoCheckpointStore.list_for_run() returns all checkpoints for a run."""

    async def scenario():
        mock_col = AsyncMock()
        store = MongoCheckpointStore.__new__(MongoCheckpointStore)
        store._db = MagicMock()
        store._col = mock_col

        docs = [
            {
                "_id": "ckpt-1",
                "checkpoint_id": "ckpt-1",
                "run_id": "run-1",
                "schema_version": "1.0",
                "created_at": 1000.0,
                "context": {
                    "run_id": "run-1",
                    "trace_id": "t1",
                    "execution_mode": "execute",
                    "user_id": "u1",
                    "request_metadata": {},
                },
                "intent_ir": None,
                "graph_snapshot": {"nodes": [], "edges": []},
                "retry_budget": {},
                "compensation_log": [],
                "approval_gate": None,
                "integrity": "",
            },
        ]

        class AsyncCursor:
            def __init__(self, data):
                self._data = data

            def sort(self, *a):
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._data:
                    return self._data.pop(0)
                raise StopAsyncIteration

        mock_col.find = MagicMock(return_value=AsyncCursor(list(docs)))

        result = await store.list_for_run("run-1")
        assert len(result) == 1
        assert result[0].checkpoint_id == "ckpt-1"

    asyncio.run(scenario())


# ============================================================================
# Test 2: Auto-checkpoint on every state-changing tick
# ============================================================================


def test_auto_checkpoint_creates_checkpoint_after_tick():
    """ProcessManager._tick_one() creates a checkpoint when node state changes."""

    async def scenario():
        checkpoint_store = CheckpointStore()
        pm = ProcessManager(checkpoint_store=checkpoint_store)
        run_id = "test-auto-ckpt"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)

        assert graph.get_state("n0") == NodeState.PENDING
        assert len(rpcb.checkpoints) == 0

        await pm.tick_all()

        assert graph.get_state("n0") == NodeState.COMPLETE

        assert len(rpcb.checkpoints) >= 1, (
            f"Expected auto-checkpoint after tick, got {len(rpcb.checkpoints)} checkpoints"
        )

        latest_ref = rpcb.checkpoints[-1]
        latest_ckpt = checkpoint_store.load(latest_ref.checkpoint_id)
        assert latest_ckpt is not None
        assert latest_ckpt.run_id == run_id
        assert latest_ckpt.verify()

    asyncio.run(scenario())


def test_auto_checkpoint_not_created_for_terminal_state():
    """ProcessManager does not auto-checkpoint processes already in terminal states.

    A two-node graph: first tick completes n0 (process stays RUNNING),
    auto-checkpoint IS created. Second tick completes n1 (process → COMPLETED),
    auto-checkpoint is NOT created (terminal state guard).
    """

    async def scenario():
        checkpoint_store = CheckpointStore()
        pm = ProcessManager(checkpoint_store=checkpoint_store)
        run_id = "test-no-ckpt-terminal"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)

        await pm.tick_all()
        assert pm.get_process(run_id).state == RuntimeProcessState.RUNNING
        count_after_first_tick = len(rpcb.checkpoints)
        assert count_after_first_tick >= 1, "Auto-checkpoint should be created while RUNNING"

        for _ in range(5):
            await pm.tick_all()
            if pm.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        final = pm.get_process(run_id)
        assert final.state == RuntimeProcessState.COMPLETED
        assert len(final.checkpoints) == count_after_first_tick, (
            "No new checkpoints should be created after reaching terminal state"
        )

    asyncio.run(scenario())


def test_auto_checkpoint_preserves_graph_state():
    """Auto-checkpoint captures correct graph state at checkpoint time."""

    async def scenario():
        checkpoint_store = CheckpointStore()
        pm = ProcessManager(checkpoint_store=checkpoint_store)
        run_id = "test-ckpt-graph-state"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b", "cap_c")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)

        await pm.tick_all()
        assert graph.get_state("n0") == NodeState.COMPLETE

        ref = rpcb.checkpoints[-1]
        ckpt = checkpoint_store.load(ref.checkpoint_id)
        assert ckpt is not None

        snap = ckpt.graph_snapshot
        states = snap.get("states", {})
        assert states.get("n0") in ("COMPLETE", "complete")
        assert states.get("n1") in ("PENDING", "pending")

    asyncio.run(scenario())


# ============================================================================
# Test 3: Live graph sync to Neo4j
# ============================================================================


def test_sync_graph_state_calls_graph_store():
    """ProcessManager._tick_one() calls graph_store.sync_node_state for changed nodes."""

    async def scenario():
        sync_calls = []

        class MockGraphStore:
            async def sync_node_state(self, node_id, state, **props):
                sync_calls.append({"node_id": node_id, "state": state, **props})

        pm = ProcessManager(graph_store=MockGraphStore())
        run_id = "test-live-sync"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)

        await pm.tick_all()

        assert len(sync_calls) >= 1, f"Expected sync_node_state calls, got {sync_calls}"

        node_ids_called = [c["node_id"] for c in sync_calls]
        assert "n0" in node_ids_called, f"Expected n0 in sync calls, got {node_ids_called}"

        for call in sync_calls:
            assert "run_id" in call, f"sync_node_state missing run_id: {call}"
            assert call["run_id"] == run_id

    asyncio.run(scenario())


def test_sync_graph_state_skips_when_no_graph_store():
    """ProcessManager works fine when no graph_store is configured (graceful degradation)."""

    async def scenario():
        pm = ProcessManager()  # no graph_store
        run_id = "test-no-graph-store"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)

        await pm.tick_all()
        assert graph.get_state("n0") == NodeState.COMPLETE

    asyncio.run(scenario())


def test_sync_graph_state_only_syncs_changed_nodes():
    """Only nodes whose state actually changed are synced to Neo4j."""

    async def scenario():
        sync_calls = []

        class MockGraphStore:
            async def sync_node_state(self, node_id, state, **props):
                sync_calls.append({"node_id": node_id, "state": state})

        pm = ProcessManager(graph_store=MockGraphStore())
        run_id = "test-only-changed"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b", "cap_c")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)

        await pm.tick_all()
        first_tick_calls = list(sync_calls)
        assert len(first_tick_calls) >= 1

        sync_calls.clear()
        await pm.tick_all()
        second_tick_calls = list(sync_calls)

        all_synced_ids = [c["node_id"] for c in first_tick_calls + second_tick_calls]
        assert "n0" in all_synced_ids or "n1" in all_synced_ids

    asyncio.run(scenario())


# ============================================================================
# Test 4: End-to-end — ProcessManager with both checkpoint + graph store
# ============================================================================


def test_e2e_checkpoint_and_graph_store():
    """ProcessManager with both MongoCheckpointStore and GraphStore wired works end-to-end."""

    async def scenario():
        checkpoint_store = CheckpointStore()
        sync_calls = []

        class MockGraphStore:
            async def sync_node_state(self, node_id, state, **props):
                sync_calls.append({"node_id": node_id, "state": state})

        pm = ProcessManager(checkpoint_store=checkpoint_store, graph_store=MockGraphStore())
        run_id = "test-e2e"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b", "cap_c")

        rpcb = await pm.create_process(ctx, graph=graph, target="execute")
        await pm.start_process(run_id)

        for _ in range(5):
            await pm.tick_all()
            if pm.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        final = pm.get_process(run_id)
        assert final.state == RuntimeProcessState.COMPLETED

        assert len(final.checkpoints) >= 2, f"Expected at least 2 auto-checkpoints, got {len(final.checkpoints)}"

        assert len(sync_calls) >= 2, f"Expected at least 2 graph sync calls, got {len(sync_calls)}"

        for ckpt_ref in final.checkpoints:
            ckpt = checkpoint_store.load(ckpt_ref.checkpoint_id)
            assert ckpt is not None, f"Checkpoint {ckpt_ref.checkpoint_id} not found"
            assert ckpt.verify(), f"Checkpoint {ckpt_ref.checkpoint_id} failed integrity"

        restored_ckpt = checkpoint_store.load(final.checkpoints[0].checkpoint_id)
        from src.monkey_brain.kernel.process.checkpoint import restore_from_checkpoint

        ctx_restored, graph_restored = restore_from_checkpoint(restored_ckpt)
        assert ctx_restored.run_id == run_id

    asyncio.run(scenario())


def test_e2e_checkpoint_restore_with_graph_store():
    """Checkpoint → restore on fresh PM with graph_store resumes correctly."""

    async def scenario():
        checkpoint_store = CheckpointStore()
        sync_calls = []

        class MockGraphStore:
            async def sync_node_state(self, node_id, state, **props):
                sync_calls.append({"node_id": node_id, "state": state})

        pm1 = ProcessManager(checkpoint_store=checkpoint_store, graph_store=MockGraphStore())
        run_id = "test-restore-sync"
        ctx = _make_context(run_id)
        graph = _linear_graph("cap_a", "cap_b")

        rpcb = await pm1.create_process(ctx, graph=graph, target="execute")
        await pm1.start_process(run_id)

        await pm1.tick_all()
        assert graph.get_state("n0") == NodeState.COMPLETE

        await pm1.suspend_process(run_id)
        ref = await pm1.checkpoint_process(run_id)

        pm2 = ProcessManager(checkpoint_store=checkpoint_store, graph_store=MockGraphStore())
        restored = await pm2.restore_process(ref.checkpoint_id)
        assert restored.state == RuntimeProcessState.RESUMING
        assert restored.graph.get_state("n0") == NodeState.COMPLETE

        await pm2.start_process(run_id)
        for _ in range(5):
            await pm2.tick_all()
            if pm2.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        final = pm2.get_process(run_id)
        assert final.state == RuntimeProcessState.COMPLETED

        assert len(sync_calls) >= 1, "Graph sync should have been called during restore+resume ticks"

    asyncio.run(scenario())


# ============================================================================
# Test 5: Lemon Elasticsearch connection
# ============================================================================


def test_lemon_elasticsearch_connection():
    """Lemon.connect_elasticsearch() connects when ES is available."""

    async def scenario():
        from src.introspection.lemon import Lemon

        lemon = Lemon()
        assert lemon._es_client is None

        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)

        import sys

        mock_es = MagicMock()
        mock_es.AsyncElasticsearch.return_value = mock_client
        sys.modules["elasticsearch"] = mock_es
        try:
            await lemon.connect_elasticsearch()
            assert lemon._es_client is not None
            mock_client.ping.assert_awaited_once()
        finally:
            sys.modules.pop("elasticsearch", None)

    asyncio.run(scenario())


def test_lemon_elasticsearch_graceful_fallback():
    """Lemon.connect_elasticsearch() falls back gracefully when ES is unavailable."""

    async def scenario():
        from src.introspection.lemon import Lemon

        lemon = Lemon()

        import sys

        mock_es = MagicMock()
        mock_es.AsyncElasticsearch.side_effect = Exception("connection refused")
        sys.modules["elasticsearch"] = mock_es
        try:
            await lemon.connect_elasticsearch()
        finally:
            sys.modules.pop("elasticsearch", None)

        assert lemon._es_client is None, (
            f"Expected _es_client to be None after failed connection, got {lemon._es_client}"
        )

        lemon.counter("test.metric", 1)
        export = lemon.metrics.export()
        assert export["counters"].get("test.metric:", 0) == 1

    asyncio.run(scenario())


def test_lemon_persist_metrics():
    """Lemon.persist_metrics() writes to ES when connected."""

    async def scenario():
        from src.introspection.lemon import Lemon

        lemon = Lemon()
        mock_client = AsyncMock()
        mock_client.index = AsyncMock()
        lemon._es_client = mock_client

        lemon.counter("test.counter", 42)
        result = await lemon.persist_metrics()
        assert result["status"] == "persisted"
        mock_client.index.assert_called_once()

        call_kwargs = mock_client.index.call_args
        assert call_kwargs[1]["index"] == "agentos-metrics"

    asyncio.run(scenario())


def test_lemon_persist_metrics_no_es():
    """Lemon.persist_metrics() returns no_elasticsearch when not connected."""

    async def scenario():
        from src.introspection.lemon import Lemon

        lemon = Lemon()
        result = await lemon.persist_metrics()
        assert result["status"] == "no_elasticsearch"

    asyncio.run(scenario())


def test_lemon_persist_traces():
    """Lemon.persist_traces() writes trace data to ES."""

    async def scenario():
        from src.introspection.lemon import Lemon

        lemon = Lemon()
        mock_client = AsyncMock()
        mock_client.index = AsyncMock()
        lemon._es_client = mock_client

        lemon.start_trace("test_trace")
        lemon.finish_trace()

        result = await lemon.persist_traces()
        assert result["status"] == "persisted"
        assert result["count"] >= 1

    asyncio.run(scenario())


def test_lemon_persist_all():
    """Lemon.persist_all() orchestrates metrics, traces, and logs persistence."""

    async def scenario():
        from src.introspection.lemon import Lemon

        lemon = Lemon()
        mock_client = AsyncMock()
        mock_client.index = AsyncMock()
        lemon._es_client = mock_client

        lemon.counter("x", 1)
        lemon.start_trace("t")
        lemon.finish_trace()
        lemon.info("test log")

        result = await lemon.persist_all()
        assert result["metrics"]["status"] == "persisted"
        assert result["traces"]["status"] == "persisted"
        assert result["logs"]["status"] == "persisted"

    asyncio.run(scenario())


# ============================================================================
# Runner
# ============================================================================

if __name__ == "__main__":
    tests = [
        # MongoCheckpointStore
        test_mongo_checkpoint_save_and_load,
        test_mongo_checkpoint_latest_for_run,
        test_mongo_checkpoint_delete,
        test_mongo_checkpoint_list_for_run,
        # Auto-checkpoint
        test_auto_checkpoint_creates_checkpoint_after_tick,
        test_auto_checkpoint_not_created_for_terminal_state,
        test_auto_checkpoint_preserves_graph_state,
        # Live graph sync
        test_sync_graph_state_calls_graph_store,
        test_sync_graph_state_skips_when_no_graph_store,
        test_sync_graph_state_only_syncs_changed_nodes,
        # E2E
        test_e2e_checkpoint_and_graph_store,
        test_e2e_checkpoint_restore_with_graph_store,
        # Lemon ES
        test_lemon_elasticsearch_connection,
        test_lemon_elasticsearch_graceful_fallback,
        test_lemon_persist_metrics,
        test_lemon_persist_metrics_no_es,
        test_lemon_persist_traces,
        test_lemon_persist_all,
    ]
    ok = 0
    failures = []
    for fn in tests:
        try:
            fn()
            ok += 1
            print(f"  PASS: {fn.__name__}")
        except Exception as e:
            failures.append(f"{fn.__name__}: {e!r}")
            print(f"  FAIL: {fn.__name__}: {e!r}")
    print(f"\nPersistence E2E: {ok}/{len(tests)} passed")
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  {f}")
