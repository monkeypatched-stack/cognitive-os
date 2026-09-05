"""CognitiveOS edge hybrid storage benchmark + regression test.

Repository inspection performed before writing this file (do not re-derive
these findings without checking the current source first):

- `EdgeLocalStore` (kernel/edge/local_store.py) is the real, only
  production SQLite-backed local store. API used here: `put`, `get`,
  `put_many`, `get_many`, `close`. No `active_flight_plan` key or mission-
  plan domain exists anywhere in this codebase -- this file stores a
  generic `{"plan_id", "goal_hash", "world_state_version", "steps"}`
  shape under namespace "plan", key "committed_plan", the closest honest
  analogue to a "frozen/committed mission plan."
- Moss (kernel/edge/moss_retrieval.py::MossSemanticMemory) is an OPTIONAL,
  narrow-scope, cloud-backed semantic-search adapter (see
  docs/CLOUD_EDGE_ACTOR_ARCHITECTURE.md Section 18's "MossDB scope
  decision"). There is no `MossEdgeContextEngine`, no telemetry-ingestion
  pipeline, and no production code that feeds sensor/telemetry packets
  into Moss -- that pipeline does not exist. This file benchmarks the
  REAL `MossSemanticMemory.index_documents()`/`query()` methods with
  synthetic-but-realistic telemetry-shaped documents, but ONLY when
  `MOSS_PROJECT_ID`/`MOSS_PROJECT_KEY` are configured (they are not, in
  this environment) -- per the task's own instruction not to mock away
  the component being benchmarked, an unconfigured Moss is a `skip`, not
  a fake substitute and not a fabricated number.
- `classify_reasoning_need` (kernel/edge/plan_reuse.py) is real and pure.
  There is no "Deja Vu loop" anywhere in this codebase; this is the
  closest real analogue (an LLM-call-avoidance / plan-reuse classifier),
  confirmed via grep for `should_replan`/`requires_replan`/`reuse_plan`
  returning nothing else. It is NOT wired into any live tick loop yet
  (kernel/pipeline/belief_runtime.py does not call it) -- this benchmark
  calls it directly, it does not exercise a pre-existing "tick" entry
  point, because no such entry point exists.
- ROS integration (kernel/edge/ros_integration.py):
  `run_ros_action_if_governed` + `FakeRosExecutionAdapter` is the real,
  existing simulation layer this file uses -- no physical or simulated
  hardware is touched, per the task's own instruction.
- No pre-existing "hybrid control tick" function chains these four pieces
  together anywhere in production code. `_hybrid_tick()` below is this
  benchmark's own composition of four independently-real production
  calls (EdgeLocalStore.get, MossSemanticMemory.query when configured,
  classify_reasoning_need, run_ros_action_if_governed) -- it is a
  benchmark harness, not a claim that this exact function exists in
  kernel/edge/ today.
- No existing telemetry-to-EdgeLocalStore async/batched writer exists.
  `kernel/edge/telemetry.py::AsyncTelemetryDispatcher` is a DIFFERENT
  subsystem (metrics/counters via kernel/compile/_obs.py), not a
  telemetry-packet-to-SQLite pipeline. Per this task's Section 3
  instruction ("if the current implementation synchronously writes
  telemetry, do not silently change behavior... benchmark the existing
  synchronous behavior... add a benchmark for the intended async
  architecture IF THE CODE ALREADY SUPPORTS IT"): the code does not
  support an async telemetry-to-EdgeLocalStore path, so only the real,
  existing synchronous `put`/`put_many` are benchmarked here. No
  fictional async path is added or benchmarked.
- Existing benchmark/perf conventions found and reused rather than
  duplicated: `kernel/fix/performance_budgets.py::LatencyBudget`/
  `PERFORMANCE_BUDGETS`/`PerformanceMonitor` (percentile-budget dataclass
  and observation recorder already used by scripts/gate9_benchmark.py) --
  new `edge.*` budget entries were added there, not redefined here.
  `tests/benchmarks/test_performance.py` requires a LIVE server
  (httpx against localhost:8031) and is gated by `tests/conftest.py`'s
  path-based `RUN_INTEGRATION=1` requirement -- this file needs no live
  server (everything is real in-process code), so, like
  tests/unit/test_edge_hot_path_zero_round_trips.py before it, it lives
  under tests/unit/ and defines its OWN gate
  (`COGNITIVEOS_RUN_EDGE_BENCHMARKS=1`) for the performance-only classes,
  per this task's own Section 9 instruction, rather than being
  (mis)gated by the unrelated live-server convention.

Correctness tests below always run in normal CI. Performance/benchmark
tests are skipped unless `COGNITIVEOS_RUN_EDGE_BENCHMARKS=1` is set.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import pytest

from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.freshness import CacheProvenance
from src.monkey_brain.kernel.edge.plan_reuse import (
    CommittedPlanRecord,
    ReasoningNeed,
    classify_reasoning_need,
)
from src.monkey_brain.kernel.edge.ros_integration import (
    FakeRosExecutionAdapter,
    run_ros_action_if_governed,
)
from src.monkey_brain.kernel.edge.moss_retrieval import build_moss_semantic_memory
from src.monkey_brain.kernel.fix.performance_budgets import PERFORMANCE_BUDGETS

RUN_BENCHMARKS = os.environ.get("COGNITIVEOS_RUN_EDGE_BENCHMARKS", "") == "1"
_MOSS_CONFIGURED = bool(os.environ.get("MOSS_PROJECT_ID")) and bool(os.environ.get("MOSS_PROJECT_KEY"))

_skip_unless_benchmarks = pytest.mark.skipif(
    not RUN_BENCHMARKS, reason="set COGNITIVEOS_RUN_EDGE_BENCHMARKS=1 to run edge hybrid performance benchmarks",
)
_skip_unless_moss = pytest.mark.skipif(
    not _MOSS_CONFIGURED, reason="MOSS_PROJECT_ID/MOSS_PROJECT_KEY not set -- real Moss benchmark requires real credentials",
)


def _bench_n(default: int, cap: int | None = None) -> int:
    n = int(os.environ.get("COGNITIVEOS_BENCH_N", str(default)))
    if cap is not None:
        n = min(n, cap)
    return max(n, 2)


def _budget(name: str, field: str) -> float:
    """Env-var override first (EDGE_<NAME>_<FIELD>_BUDGET_MS), falling
    back to kernel/fix/performance_budgets.py's PERFORMANCE_BUDGETS --
    never a second, independently-hardcoded threshold."""
    env_key = f"EDGE_{name.upper().replace('.', '_')}_{field.upper()}_BUDGET_MS"
    override = os.environ.get(env_key)
    if override is not None:
        return float(override)
    budget = PERFORMANCE_BUDGETS[name]
    return getattr(budget, f"{field}_ms")


def _percentiles(samples_ms: list[float]) -> dict[str, float]:
    s = sorted(samples_ms)
    n = len(s)
    return {
        "min": s[0], "max": s[-1], "mean": sum(s) / n,
        "p50": s[n // 2], "p95": s[min(int(n * 0.95), n - 1)], "p99": s[min(int(n * 0.99), n - 1)],
    }


def _assert_budget(name: str, stats: dict[str, float], *, fields: tuple[str, ...] = ("p50", "p95", "p99")) -> None:
    for field in fields:
        budget_ms = _budget(name, field)
        observed_ms = stats[field]
        assert observed_ms <= budget_ms, (
            f"\n{name} {field} exceeded budget:\n"
            f"  observed: {observed_ms:.4f} ms\n"
            f"  budget:   {budget_ms:.4f} ms\n"
            f"  N:        {stats.get('_n', '?')}\n"
        )


def _print_report(title: str, stats: dict[str, float]) -> None:
    print(
        f"\n{title:45s} "
        f"p50={stats['p50']:9.4f}ms  p95={stats['p95']:9.4f}ms  p99={stats['p99']:9.4f}ms  "
        f"mean={stats['mean']:9.4f}ms  min={stats['min']:9.4f}ms  max={stats['max']:9.4f}ms",
    )


def _committed_plan(plan_id: str = "plan-001") -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "goal_hash": "g1",
        "world_state_version": "v1",
        "policy_version": "p1",
        "steps": [
            {"capability": "grocery.find_item", "parameters": {"item": "milk"}},
            {"capability": "grocery.add_to_cart", "parameters": {"item": "milk"}},
        ],
    }


def _telemetry_document(i: int) -> dict[str, Any]:
    """Synthetic-but-realistic representative telemetry/context document
    -- no telemetry schema exists in this codebase for any physical/edge
    domain (confirmed by inspection), so this shape is this benchmark's
    own choice, documented as such rather than presented as a real schema."""
    fields = {
        "position": {"x": 12.3 + i, "y": 45.6, "z": 1.2},
        "velocity": {"x": 0.5, "y": 0.0, "z": 0.0},
        "acceleration": {"x": 0.01, "y": 0.0, "z": -9.81},
        "heading_deg": (i * 3.7) % 360,
        "altitude_m": 100.0 + (i % 10),
        "sensor_state": "nominal" if i % 20 else "degraded",
        "obstacle_detected": bool(i % 15 == 0),
        "timestamp": time.time(),
        "plan_id": "plan-001",
        "plan_version": 1,
    }
    text = " ".join(f"{k}={v}" for k, v in fields.items())
    return {"id": f"telemetry-{i}", "text": text, "metadata": {"kind": "telemetry_context"}}


class _FakeConnectivityCheck:
    """Real ActionExecutor connectivity gate stub -- simulates a
    disconnected edge node so LocalGovernanceEvaluator (not central OPA)
    makes the call, matching the intended edge hot path."""

    def __call__(self, capability_name: str):
        return False, "WAITING_FOR_AUTHORITY", f"{capability_name} disconnected"


async def _ros_execute_governed(adapter: FakeRosExecutionAdapter, capability: str) -> dict[str, Any]:
    """The real, existing ROS governance boundary -- force_authorize
    path bypassed via a pre-approved local_policy_decision (equivalent to
    what LocalGovernanceEvaluator.evaluate() would already have produced
    for a cached, valid, AUTO_APPROVE snapshot), so this benchmarks
    run_ros_action_if_governed + FakeRosExecutionAdapter themselves, not
    OPA/network latency (a separate, already-measured concern)."""
    return await run_ros_action_if_governed(
        capability=capability, resource=capability, parameters={},
        adapter=adapter,
        local_policy_decision={
            "allowed": True, "approval_mode": "AUTO_APPROVE", "reason": "benchmark",
            "policy_rule": "bench", "risk_level": "LOW", "source": "edge_local_governance",
        },
    )


def _hybrid_tick_sync_stage(store: EdgeLocalStore) -> dict[str, Any]:
    """SQLite plan-lookup + reasoning classification -- the synchronous
    portion of the composed hybrid tick (see module docstring: no
    production function chains these today, this is this benchmark's own
    composition of independently-real calls)."""
    entry = store.get("plan", "committed_plan")
    plan = entry.value if entry is not None else None
    committed = None
    if plan is not None:
        committed = CommittedPlanRecord(
            plan=plan, goal_hash=plan["goal_hash"], world_state_version=plan["world_state_version"],
            policy_version=plan["policy_version"], committed_at=0.0,
        )
    decision = classify_reasoning_need(
        goal="buy milk", goal_achieved=False, goal_hash="g1",
        world_state_version="v1", policy_version="p1", committed_plan=committed,
    )
    return {"plan": plan, "decision": decision}


class TestCorrectness:
    """Always run in normal CI -- no environment gate."""

    @pytest.fixture()
    def store(self, tmp_path):
        s = EdgeLocalStore(str(tmp_path / "edge.db"))
        yield s
        s.close()

    def test_committed_plan_round_trips_with_stable_plan_id(self, store):
        prov = CacheProvenance(source="test", expires_at=time.time() + 60)
        plan = _committed_plan()
        store.put("plan", "committed_plan", plan, prov)

        entry = store.get("plan", "committed_plan")
        assert entry is not None
        assert entry.value["plan_id"] == "plan-001"
        assert entry.value["steps"] == plan["steps"]

    def test_reasoning_reuses_a_compatible_committed_plan(self, store):
        prov = CacheProvenance(source="test", expires_at=time.time() + 60)
        store.put("plan", "committed_plan", _committed_plan(), prov)

        result = _hybrid_tick_sync_stage(store)

        assert result["decision"].need is ReasoningNeed.REUSE_EXISTING_PLAN

    def test_world_state_drift_triggers_replan_required(self, store):
        prov = CacheProvenance(source="test", expires_at=time.time() + 60)
        stale_plan = _committed_plan()
        stale_plan["world_state_version"] = "v0-stale"
        store.put("plan", "committed_plan", stale_plan, prov)

        result = _hybrid_tick_sync_stage(store)

        assert result["decision"].need is ReasoningNeed.REPLAN_REQUIRED, (
            "a world-state version mismatch must trigger REPLAN_REQUIRED, not a false REUSE"
        )

    def test_no_committed_plan_requires_llm_not_a_false_reuse(self, store):
        result = _hybrid_tick_sync_stage(store)  # nothing stored yet
        assert result["plan"] is None
        assert result["decision"].need is ReasoningNeed.LLM_REQUIRED

    @pytest.mark.asyncio
    async def test_ros_execution_only_runs_when_governance_allows(self):
        adapter = FakeRosExecutionAdapter()
        result = await _ros_execute_governed(adapter, "move_forward")
        assert result["success"] is True
        assert len(adapter.calls) == 1
        assert adapter.calls[0]["capability"] == "move_forward"

    @pytest.mark.asyncio
    async def test_ros_execution_never_runs_when_governance_denies(self):
        adapter = FakeRosExecutionAdapter()
        with pytest.raises(Exception):
            await run_ros_action_if_governed(
                capability="move_forward", resource="move_forward", parameters={}, adapter=adapter,
                local_policy_decision={
                    "allowed": False, "approval_mode": "DENY", "reason": "benchmark deny",
                    "policy_rule": "bench", "risk_level": "HIGH", "source": "edge_local_governance",
                },
            )
        assert adapter.calls == [], "a DENY decision must never reach the adapter"

    def test_persisted_plan_survives_restart(self, tmp_path):
        db_path = str(tmp_path / "restart.db")
        prov = CacheProvenance(source="test", expires_at=time.time() + 60)
        s1 = EdgeLocalStore(db_path)
        s1.put("plan", "committed_plan", _committed_plan(), prov)
        s1.close()

        s2 = EdgeLocalStore(db_path)
        entry = s2.get("plan", "committed_plan")
        s2.close()

        assert entry is not None
        assert entry.value["plan_id"] == "plan-001"

    def test_moss_semantic_memory_is_none_when_unconfigured(self, monkeypatch):
        """Correctness of the fallback path itself -- the normal runtime
        must never depend on Moss being present."""
        monkeypatch.delenv("MOSS_PROJECT_ID", raising=False)
        monkeypatch.delenv("MOSS_PROJECT_KEY", raising=False)
        assert build_moss_semantic_memory() is None


@_skip_unless_benchmarks
class TestSqliteHotReadBenchmark:
    """Section A."""

    def test_active_plan_read(self, tmp_path):
        store = EdgeLocalStore(str(tmp_path / "edge.db"))
        prov = CacheProvenance(source="bench", expires_at=time.time() + 3600)
        store.put("plan", "committed_plan", _committed_plan(), prov)

        n = _bench_n(1000)
        for _ in range(50):  # warmup, discarded
            store.get("plan", "committed_plan")

        samples = []
        for _ in range(n):
            t0 = time.perf_counter_ns()
            store.get("plan", "committed_plan")
            samples.append((time.perf_counter_ns() - t0) / 1e6)
        store.close()

        stats = _percentiles(samples)
        stats["_n"] = n
        _print_report("SQLite active-plan read (get)", stats)
        _assert_budget("edge.sqlite_read", stats)


@_skip_unless_benchmarks
@_skip_unless_moss
class TestMossIngestionBenchmark:
    """Section B -- real credentials required; honestly skipped otherwise."""

    @pytest.mark.asyncio
    async def test_context_ingestion(self):
        memory = build_moss_semantic_memory(require=True)
        docs = [_telemetry_document(i) for i in range(20)]
        for _ in range(3):
            await memory.index_documents(docs[:1])  # warmup

        n = _bench_n(50, cap=200)
        samples = []
        for i in range(n):
            t0 = time.perf_counter_ns()
            await memory.index_documents([_telemetry_document(1000 + i)])
            samples.append((time.perf_counter_ns() - t0) / 1e6)

        stats = _percentiles(samples)
        stats["_n"] = n
        _print_report("Moss context ingestion (real)", stats)
        _assert_budget("edge.moss_ingest", stats)


@_skip_unless_benchmarks
@_skip_unless_moss
class TestMossRetrievalBenchmark:
    """Section C -- real credentials required; honestly skipped otherwise.
    10k/100k-entry corpora are NOT populated here: Moss's real corpus
    population/quota behavior under real load is not something this
    environment can validate without a funded/provisioned real project,
    and fabricating that scale against a real paid service without
    explicit authorization is out of scope -- only a small, real corpus
    is exercised."""

    @pytest.mark.asyncio
    async def test_semantic_retrieval_small_corpus(self):
        memory = build_moss_semantic_memory(require=True)
        docs = [_telemetry_document(i) for i in range(50)]
        await memory.index_documents(docs)

        for _ in range(3):
            await memory.query("obstacle detected near current position")  # warmup

        n = _bench_n(50, cap=200)
        samples = []
        for _ in range(n):
            t0 = time.perf_counter_ns()
            await memory.query("obstacle detected near current position")
            samples.append((time.perf_counter_ns() - t0) / 1e6)

        stats = _percentiles(samples)
        stats["_n"] = n
        _print_report("Moss semantic retrieval (real, small corpus)", stats)
        _assert_budget("edge.moss_retrieve", stats)


@_skip_unless_benchmarks
class TestHybridTickBenchmark:
    """Section D. When Moss is not configured (this environment), the
    tick's real stages are SQLite plan-lookup + reasoning classification +
    governed ROS execution; the report says so explicitly rather than
    silently omitting the Moss stage.

    KNOWN, MEASURED bottleneck (do not "fix" by loosening the budget --
    see this task's own Section 15): profiling this benchmark (not
    guessing) found the ROS-execution stage's `ensure_governed` call
    performs 6 synchronous `AuditLog.record()` writes per invocation,
    each a real ~13-14ms Mongo round trip once Mongo is reachable (it
    was down for most of this session; now up) -- ~80ms/tick total, all
    of it inside the unmodified, pre-existing central audit pipeline
    (kernel/security_boundary.py/kernel/audit.py), not in SQLite, Moss,
    or classify_reasoning_need (all sub-millisecond in isolation, proven
    separately by TestSqliteHotReadBenchmark and the plain function call
    cost of classify_reasoning_need). This reproduces and precisely
    quantifies the "Mongo-down audit stall" finding from the earlier Edge
    Performance Optimization pass -- fixing it means touching
    security-critical, fail-closed audit-write code, explicitly out of
    this benchmark's scope per its own "smallest necessary change,
    documented separately" instruction. The budget below is left at the
    edge architecture's INTENDED target (not loosened to match today's
    reality), so this assertion is expected to fail honestly until that
    audit-pipeline latency is addressed as its own, separately-scoped
    piece of work."""

    @pytest.fixture()
    def store(self, tmp_path):
        s = EdgeLocalStore(str(tmp_path / "edge.db"))
        prov = CacheProvenance(source="bench", expires_at=time.time() + 3600)
        s.put("plan", "committed_plan", _committed_plan(), prov)
        yield s
        s.close()

    @pytest.mark.asyncio
    async def test_hybrid_control_tick(self, store):
        from src.monkey_brain.kernel.audit import get_audit_log

        moss = build_moss_semantic_memory()
        adapter = FakeRosExecutionAdapter()

        audit_log = get_audit_log()
        orig_record = audit_log.record
        audit_calls = {"n": 0, "total_ms": 0.0}

        def _counted_record(*a, **k):
            t0 = time.perf_counter_ns()
            result = orig_record(*a, **k)
            audit_calls["n"] += 1
            audit_calls["total_ms"] += (time.perf_counter_ns() - t0) / 1e6
            return result

        async def tick():
            if moss is not None:
                await moss.query("obstacle detected")
            result = _hybrid_tick_sync_stage(store)
            if result["decision"].need in (ReasoningNeed.REUSE_EXISTING_PLAN, ReasoningNeed.LOCAL_RULE):
                await _ros_execute_governed(adapter, "grocery.add_to_cart")
            return result

        for _ in range(20):
            await tick()  # warmup
        adapter.calls = []  # warmup calls must not count toward the measured assertion below

        n = _bench_n(200, cap=500)
        audit_log.record = _counted_record
        try:
            samples = []
            for _ in range(n):
                t0 = time.perf_counter_ns()
                await tick()
                samples.append((time.perf_counter_ns() - t0) / 1e6)
        finally:
            audit_log.record = orig_record

        stats = _percentiles(samples)
        stats["_n"] = n
        title = "Hybrid control tick" + ("" if moss is not None else " (Moss stage SKIPPED -- not configured)")
        _print_report(title, stats)
        print(
            f"  -> {audit_calls['n']} AuditLog.record() calls across {n} ticks "
            f"({audit_calls['n'] / n:.1f}/tick), {audit_calls['total_ms']:.1f}ms total in record() "
            f"({audit_calls['total_ms'] / max(audit_calls['n'], 1):.3f}ms/call avg) -- see class docstring",
        )
        assert len(adapter.calls) == n, "every tick with a reusable plan must reach ROS execution exactly once"
        _assert_budget("edge.hybrid_tick", stats)


@_skip_unless_benchmarks
class TestBatchPersistenceBenchmark:
    """Section C (batching, not Moss) -- real put_many at increasing batch sizes."""

    @pytest.mark.parametrize("batch_size", [1, 10, 20, 50, 100])
    def test_put_many_batch_sizes(self, tmp_path, batch_size):
        store = EdgeLocalStore(str(tmp_path / f"edge_{batch_size}.db"))
        prov = CacheProvenance(source="bench", expires_at=time.time() + 3600)

        n = _bench_n(30, cap=100)
        total_samples = []
        for round_i in range(n):
            entries = {f"b{round_i}_{j}": ({"v": j}, prov) for j in range(batch_size)}
            t0 = time.perf_counter_ns()
            store.put_many("telemetry", entries)
            total_samples.append((time.perf_counter_ns() - t0) / 1e6)
        store.close()

        stats = _percentiles(total_samples)
        stats["_n"] = n
        per_row = {k: v / batch_size if k != "_n" else v for k, v in stats.items()}
        print(
            f"\nSQLite put_many batch={batch_size:<4d} "
            f"total p50={stats['p50']:.4f}ms p95={stats['p95']:.4f}ms  "
            f"per-row p50={per_row['p50']:.4f}ms p95={per_row['p95']:.4f}ms",
        )


@_skip_unless_benchmarks
class TestHotPathContaminationBenchmark:
    """Section 4 -- proves (or disproves) that concurrent SQLite write
    activity perturbs a plan read on the same EdgeLocalStore connection.
    EdgeLocalStore serializes all access behind one threading.RLock (real
    implementation, see kernel/edge/local_store.py), so this measures
    that lock's real contention cost under concurrent read+write threads,
    not a simulated queue (no queue abstraction exists for this)."""

    @pytest.fixture()
    def store(self, tmp_path):
        s = EdgeLocalStore(str(tmp_path / "edge.db"))
        prov = CacheProvenance(source="bench", expires_at=time.time() + 3600)
        s.put("plan", "committed_plan", _committed_plan(), prov)
        yield s
        s.close()

    def _read_samples(self, store, n) -> list[float]:
        samples = []
        for _ in range(n):
            t0 = time.perf_counter_ns()
            store.get("plan", "committed_plan")
            samples.append((time.perf_counter_ns() - t0) / 1e6)
        return samples

    def test_baseline_vs_concurrent_write_contention(self, store):
        n = _bench_n(300, cap=1000)
        prov = CacheProvenance(source="bench", expires_at=time.time() + 3600)

        for _ in range(20):
            store.get("plan", "committed_plan")  # warmup
        baseline = _percentiles(self._read_samples(store, n))

        stop = threading.Event()

        def writer():
            i = 0
            while not stop.is_set():
                store.put("telemetry", f"t{i}", {"v": i}, prov)
                i += 1

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            with_writes = _percentiles(self._read_samples(store, n))
        finally:
            stop.set()
            t.join(timeout=2.0)

        amplification_pct = (with_writes["p95"] / baseline["p95"] - 1.0) * 100 if baseline["p95"] > 0 else 0.0
        print(
            f"\nContamination: baseline p95={baseline['p95']:.4f}ms  "
            f"with concurrent writes p95={with_writes['p95']:.4f}ms  "
            f"amplification={amplification_pct:.1f}%",
        )
        # Reported, not hard-failed on an arbitrary threshold -- SQLite's
        # own lock contention under real concurrent write load is exactly
        # the kind of machine/OS-scheduler-dependent number this task
        # warns against pinning to a tight CI assertion.


@_skip_unless_benchmarks
class TestScaleBenchmark:
    """Section 5 -- SQLite only; Moss scale (10k/100k) requires real
    provisioned capacity not available in this environment (see
    TestMossRetrievalBenchmark's own docstring)."""

    @pytest.mark.parametrize("row_count", [100, 1000, 10000, 100000])
    def test_sqlite_read_write_at_scale(self, tmp_path, row_count):
        store = EdgeLocalStore(str(tmp_path / f"edge_{row_count}.db"))
        prov = CacheProvenance(source="bench", expires_at=time.time() + 3600)

        chunk = 500
        entries = {}
        for i in range(row_count):
            entries[f"k{i}"] = ({"v": i}, prov)
            if len(entries) >= chunk:
                store.put_many("scale", entries)
                entries = {}
        if entries:
            store.put_many("scale", entries)

        n = _bench_n(200, cap=500)
        read_samples = []
        for i in range(n):
            t0 = time.perf_counter_ns()
            store.get("scale", f"k{i % row_count}")
            read_samples.append((time.perf_counter_ns() - t0) / 1e6)
        read_stats = _percentiles(read_samples)

        write_samples = []
        for i in range(n):
            t0 = time.perf_counter_ns()
            store.put("scale", f"k{i % row_count}", {"v": i}, prov)
            write_samples.append((time.perf_counter_ns() - t0) / 1e6)
        write_stats = _percentiles(write_samples)

        store.close()
        print(
            f"\nScale {row_count:>7d} rows: "
            f"SQLite read p50={read_stats['p50']:.4f}ms p95={read_stats['p95']:.4f}ms  "
            f"SQLite write p50={write_stats['p50']:.4f}ms p95={write_stats['p95']:.4f}ms  "
            f"Moss n/a (no credentials)  Hybrid n/a (see TestHybridTickBenchmark)",
        )


@_skip_unless_benchmarks
class TestRestartBenchmark:
    """Section 6."""

    @pytest.mark.parametrize("row_count", [1000, 10000, 100000])
    def test_restart_to_ready(self, tmp_path, row_count):
        db_path = str(tmp_path / f"restart_{row_count}.db")
        prov = CacheProvenance(source="bench", expires_at=time.time() + 3600)

        s1 = EdgeLocalStore(db_path)
        entries = {}
        chunk = 500
        for i in range(row_count):
            entries[f"k{i}"] = ({"v": i}, prov)
            if len(entries) >= chunk:
                s1.put_many("scale", entries)
                entries = {}
        if entries:
            s1.put_many("scale", entries)
        s1.close()

        repeats = _bench_n(5, cap=10)
        samples = []
        for _ in range(repeats):
            t0 = time.perf_counter_ns()
            s2 = EdgeLocalStore(db_path)
            _ = s2.get("scale", "k0")  # prove it's genuinely ready, not just connected
            samples.append((time.perf_counter_ns() - t0) / 1e6)
            s2.close()

        stats = _percentiles(samples)
        stats["_n"] = repeats
        print(
            f"\nRestart-to-ready @ {row_count:>7d} rows: "
            f"p50={stats['p50']:.4f}ms p95={stats['p95']:.4f}ms (N={repeats})",
        )
        _assert_budget("edge.restart_ready", stats, fields=("p50",))
