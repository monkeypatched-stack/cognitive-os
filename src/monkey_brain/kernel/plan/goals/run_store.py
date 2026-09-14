"""RunStore — record of compiled IntentIRs, keyed by run_id.

Enables Replay(run_id): reconstruct IntentIR + ExecutionContext + Goal +
Parameters for a prior /plan call and re-run the exact compiled plan without
ever re-running intent classification.

Each stored run is tagged with a `target` ("execute" | "simulate" | "compare" | "codegen")
recording which runtime originally compiled it, since compilation is shared
across all three runtimes (see cognitive_runtime.CognitiveRuntime.compile_intent)
but /replay must dispatch back to the SAME runtime's run() to reproduce the
original behavior — replaying an "execute" run through the simulator, or vice
versa, would not be a replay of what actually happened.

Backend (single-worker vs multi-worker):

    In-memory (default): process-local, thread-safe, LRU-bounded. Correct and
    fast for a SINGLE worker.

    Redis (shared): every worker sees every run. REQUIRED for multi-worker
    deployments (gunicorn/uvicorn --workers N), because /plan, /execute,
    /simulate, /compare and /learn may each land on a different worker. With the
    in-memory backend, /execute on worker A stores the execution graph in A's
    memory and /learn on worker B never sees it — so latest_graph() returns None
    and structural learning silently degrades. The Redis backend fixes that.

    Selection: RUN_STORE_BACKEND = "auto" (default) uses Redis when REDIS_URL is
    set AND reachable, else falls back to in-memory; "redis" forces Redis (and
    logs loudly if unreachable); "memory" forces in-memory even when Redis is
    available. Redis entries expire after RUN_STORE_TTL_SECONDS (default 1 day) —
    the working set is small and short-lived, and the durable graph copy lives in
    PlanStore, so an evicted run 404s on /replay exactly as an unknown run_id does.
"""

from __future__ import annotations

import json
import logging
import copy
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR

logger = logging.getLogger("agentos.run_store")

VALID_TARGETS = frozenset({"execute", "simulate", "compare", "codegen"})

# The store is process-local and every /plan adds an entry that was NEVER removed —
# no delete, no eviction — so on a long-running server both maps grew without bound.
# Bounded LRU: the working set (a run about to be executed, simulated, or replayed) is
# small and short-lived, and the durable graph copy lives in PlanStore, so evicting a
# cold run's in-memory record is safe. /replay of an evicted run 404s, which it already
# does for any unknown run_id (the store is documented as non-durable).
DEFAULT_RUN_STORE_CAPACITY = int(os.getenv("RUN_STORE_CAPACITY", "10000"))
DEFAULT_OUTCOMES_CAPACITY = int(os.getenv("OUTCOMES_STORE_CAPACITY", "10000"))


@dataclass(frozen=True)
class StoredRun:
    intent_ir: IntentIR
    target: str
    graph_snapshot: dict[str, Any] | None = None


# ── In-memory backend (single-worker default) ────────────────────────────────
class _InMemoryRunBackend:
    """Process-local, thread-safe, LRU-bounded run_id -> StoredRun map.

    This is the exact behavior the RunStore has always had; it is now one of two
    interchangeable backends. Correct for a single worker only — see module docstring.
    """

    def __init__(self) -> None:
        # OrderedDict so a store/update can move a run to the MRU end and eviction pops
        # the LRU front. A plain dict preserved insertion order but never moved an updated
        # key, so latest_graph() below picked the wrong run after an in-place update.
        self._runs: OrderedDict[str, StoredRun] = OrderedDict()
        self._outcomes: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._capacity = DEFAULT_RUN_STORE_CAPACITY
        self._outcomes_capacity = DEFAULT_OUTCOMES_CAPACITY
        self._lock = threading.Lock()

    def _evict_locked(self) -> None:
        """Drop least-recently-used runs (and their outcomes) past capacity.
        Caller must hold self._lock."""
        while len(self._runs) > self._capacity:
            old_id, _ = self._runs.popitem(last=False)
            self._outcomes.pop(old_id, None)

    def _evict_outcomes_locked(self) -> None:
        """Drop least-recently-used outcomes past capacity.
        Caller must hold self._lock."""
        while len(self._outcomes) > self._outcomes_capacity:
            self._outcomes.popitem(last=False)

    def store(
        self,
        run_id: str,
        ir: IntentIR,
        target: str,
        graph_snapshot: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            self._runs[run_id] = StoredRun(
                intent_ir=ir,
                target=target,
                graph_snapshot=copy.deepcopy(graph_snapshot),
            )
            self._runs.move_to_end(run_id)
            self._evict_locked()

    def get(self, run_id: str) -> IntentIR | None:
        with self._lock:
            stored = self._runs.get(run_id)
            return stored.intent_ir if stored else None

    def get_target(self, run_id: str) -> str | None:
        with self._lock:
            stored = self._runs.get(run_id)
            return stored.target if stored else None

    def get_graph(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            stored = self._runs.get(run_id)
            return copy.deepcopy(stored.graph_snapshot) if stored and stored.graph_snapshot is not None else None

    def store_graph(self, run_id: str, graph_snapshot: dict[str, Any], target: str | None) -> None:
        with self._lock:
            stored = self._runs.get(run_id)
            if stored is None:
                raise KeyError(f"run {run_id!r} has no stored IntentIR")
            self._runs[run_id] = StoredRun(
                intent_ir=stored.intent_ir,
                target=target or stored.target,
                graph_snapshot=copy.deepcopy(graph_snapshot),
            )
            # Updating a graph is a use — make it the most-recently-used so
            # latest_graph() sees it as latest and eviction doesn't reclaim it next.
            self._runs.move_to_end(run_id)

    def store_outcome(self, run_id: str, outcome: dict[str, Any]) -> None:
        if run_id:
            # These two were the only methods NOT taking the lock, while the class
            # advertises "thread-safe" — a concurrent store_outcome/get_outcome raced
            # every other locked accessor. Hold the same lock.
            with self._lock:
                self._outcomes[run_id] = dict(outcome)
                self._evict_outcomes_locked()

    def get_outcome(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            outcome = self._outcomes.get(run_id)
            return dict(outcome) if outcome is not None else None

    def has(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs

    def remove(self, run_id: str) -> bool:
        with self._lock:
            self._outcomes.pop(run_id, None)
            return self._runs.pop(run_id, None) is not None

    def latest_graph(self, *, target: str | None, graph_type: str | None) -> dict[str, Any] | None:
        with self._lock:
            for run_id in reversed(list(self._runs)):
                stored = self._runs[run_id]
                snapshot = stored.graph_snapshot
                if not snapshot or not snapshot.get("nodes"):
                    continue
                if target is not None and stored.target != target:
                    continue
                if graph_type is not None and snapshot.get("graph_type") != graph_type:
                    continue
                return copy.deepcopy(snapshot)
        return None

    def run_ids(self) -> list[str]:
        with self._lock:
            return list(self._runs)

    def get_run(self, run_id: str) -> StoredRun | None:
        with self._lock:
            return self._runs.get(run_id)


# ── Redis backend (shared, multi-worker safe) ────────────────────────────────
class _RedisRunBackend:
    """Redis-backed run store: every worker reads/writes the same authoritative state.

    Keys (all TTL'd so memory is bounded without an explicit LRU):
        runstore:run:{run_id}     -> JSON {intent_ir, target, graph_snapshot}
        runstore:outcome:{run_id} -> JSON outcome
        runstore:index            -> ZSET run_id -> seq (for latest_graph, newest first)
        runstore:seq              -> INCR counter (globally monotonic across workers,
                                     so ordering does not depend on any worker's clock)

    Every op is defensive: a Redis error degrades to "not found" (None/False) for reads
    and a logged, swallowed no-op for writes — never a crashed request. The durable
    PlanStore still holds the graph for recovery, so a dropped write is not data loss.
    """

    RUN_PREFIX = "runstore:run:"
    OUT_PREFIX = "runstore:outcome:"
    INDEX_KEY = "runstore:index"
    SEQ_KEY = "runstore:seq"

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None
        self._ttl = int(os.getenv("RUN_STORE_TTL_SECONDS", "86400"))
        self._scan_cap = int(os.getenv("RUN_STORE_LATEST_SCAN", "500"))

    def _connect(self) -> Any:
        import redis  # redis-py; a declared dependency (pyproject.toml)

        # Bound both connect and per-op sockets — redis-py defaults socket_timeout to
        # None, so a HUNG server would block the calling thread forever.
        return redis.from_url(
            self._url,
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
            health_check_interval=int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL_SEC", "30")),
        )

    def available(self) -> bool:
        """Check if Redis is reachable. Can be called multiple times to retry."""
        try:
            client = self._connect()
            client.ping()
            self._client = client  # Only cache on success
            return True
        except Exception as exc:
            logger.warning(
                "RunStore Redis backend unreachable: %s (will retry on next operation)",
                exc,
            )
            # Leave _client as None (don't cache failure)
            # Next property access to _r will attempt reconnection
            return False

    @property
    def _r(self) -> Any:
        if self._client is None:
            logger.debug("Attempting to connect to Redis...")
            try:
                self._client = self._connect()
                self._client.ping()
            except Exception as exc:
                logger.warning("Redis connection failed: %s", exc)
                self._client = None
        return self._client

    def _load(self, run_id: str) -> dict[str, Any] | None:
        raw = self._r.get(self.RUN_PREFIX + run_id)
        return json.loads(raw) if raw else None

    def store(
        self,
        run_id: str,
        ir: IntentIR,
        target: str,
        graph_snapshot: dict[str, Any] | None,
    ) -> None:
        payload = json.dumps(
            {
                "intent_ir": ir.to_dict() if hasattr(ir, "to_dict") else None,
                "target": target,
                "graph_snapshot": graph_snapshot,
            }
        )
        try:
            seq = self._r.incr(self.SEQ_KEY)
            pipe = self._r.pipeline()
            pipe.set(self.RUN_PREFIX + run_id, payload, ex=self._ttl)
            pipe.zadd(self.INDEX_KEY, {run_id: seq})
            # Bound the index the way the in-memory LRU bounds its map: keep the newest N.
            pipe.zremrangebyrank(self.INDEX_KEY, 0, -(DEFAULT_RUN_STORE_CAPACITY + 1))
            pipe.execute()
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).store failed: %s", run_id, exc)

    def get(self, run_id: str) -> IntentIR | None:
        try:
            d = self._load(run_id)
            return IntentIR.from_dict(d["intent_ir"]) if d and d.get("intent_ir") else None
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).get failed: %s", run_id, exc)
            return None

    def get_target(self, run_id: str) -> str | None:
        try:
            d = self._load(run_id)
            return d.get("target") if d else None
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).get_target failed: %s", run_id, exc)
            return None

    def get_graph(self, run_id: str) -> dict[str, Any] | None:
        try:
            d = self._load(run_id)
            snap = d.get("graph_snapshot") if d else None
            return snap if snap is not None else None
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).get_graph failed: %s", run_id, exc)
            return None

    def store_graph(self, run_id: str, graph_snapshot: dict[str, Any], target: str | None) -> None:
        try:
            d = self._load(run_id)
        except Exception as exc:
            # Can't confirm the run is absent, so don't raise KeyError (which would 500 a
            # request whose plan really was stored) — log and skip the update.
            logger.warning("run=%r RunStore(redis).store_graph read failed: %s", run_id, exc)
            return
        if d is None:
            # Confirmed missing — same contract as the in-memory backend.
            raise KeyError(f"run {run_id!r} has no stored IntentIR")
        d["graph_snapshot"] = graph_snapshot
        if target:
            d["target"] = target
        try:
            seq = self._r.incr(self.SEQ_KEY)
            pipe = self._r.pipeline()
            pipe.set(self.RUN_PREFIX + run_id, json.dumps(d), ex=self._ttl)
            pipe.zadd(self.INDEX_KEY, {run_id: seq})  # bump to MRU
            pipe.execute()
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).store_graph write failed: %s", run_id, exc)

    def store_outcome(self, run_id: str, outcome: dict[str, Any]) -> None:
        if not run_id:
            return
        try:
            self._r.set(self.OUT_PREFIX + run_id, json.dumps(dict(outcome)), ex=self._ttl)
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).store_outcome failed: %s", run_id, exc)

    def get_outcome(self, run_id: str) -> dict[str, Any] | None:
        try:
            raw = self._r.get(self.OUT_PREFIX + run_id)
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).get_outcome failed: %s", run_id, exc)
            return None

    def has(self, run_id: str) -> bool:
        try:
            return bool(self._r.exists(self.RUN_PREFIX + run_id))
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).has failed: %s", run_id, exc)
            return False

    def remove(self, run_id: str) -> bool:
        try:
            existed = bool(self._r.exists(self.RUN_PREFIX + run_id))
            pipe = self._r.pipeline()
            pipe.delete(self.RUN_PREFIX + run_id)
            pipe.delete(self.OUT_PREFIX + run_id)
            pipe.zrem(self.INDEX_KEY, run_id)
            pipe.execute()
            return existed
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).remove failed: %s", run_id, exc)
            return False

    def latest_graph(self, *, target: str | None, graph_type: str | None) -> dict[str, Any] | None:
        try:
            run_ids = self._r.zrevrange(self.INDEX_KEY, 0, self._scan_cap - 1)
        except Exception as exc:
            logger.warning("RunStore(redis).latest_graph index read failed: %s", exc)
            return None
        stale: list[str] = []
        match: dict[str, Any] | None = None
        for run_id in run_ids:
            try:
                d = self._load(run_id)
            except Exception as exc:
                logger.warning("RunStore(redis).latest_graph load failed: %s", exc)
                break
            if d is None:
                # Expired via TTL but still indexed — prune it lazily.
                stale.append(run_id)
                continue
            snapshot = d.get("graph_snapshot")
            if not snapshot or not snapshot.get("nodes"):
                continue
            if target is not None and d.get("target") != target:
                continue
            if graph_type is not None and snapshot.get("graph_type") != graph_type:
                continue
            match = snapshot
            break
        if stale:
            try:
                self._r.zrem(self.INDEX_KEY, *stale)
            except Exception:
                logger.debug("latest_graph: suppressed exception", exc_info=True)
        return match

    def run_ids(self) -> list[str]:
        try:
            # Ascending by seq = oldest -> newest, matching the in-memory OrderedDict order.
            return list(self._r.zrange(self.INDEX_KEY, 0, -1))
        except Exception as exc:
            logger.warning("RunStore(redis).run_ids failed: %s", exc)
            return []

    def get_run(self, run_id: str) -> StoredRun | None:
        try:
            d = self._load(run_id)
            if not d:
                return None
            ir = IntentIR.from_dict(d["intent_ir"]) if d.get("intent_ir") else None
            return StoredRun(
                intent_ir=ir,
                target=d.get("target", ""),
                graph_snapshot=d.get("graph_snapshot"),
            )
        except Exception as exc:
            logger.warning("run=%r RunStore(redis).get_run failed: %s", run_id, exc)
            return None


def _redact(url: str) -> str:
    """Hide any credentials in a redis URL before logging it."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def _make_backend() -> Any:
    """Pick the RunStore backend from RUN_STORE_BACKEND + REDIS_URL. Decided once."""
    choice = os.getenv("RUN_STORE_BACKEND", "auto").strip().lower()
    # REDIS_URL is the explicit override; PlanetaryRuntime's own Redis
    # connection (kernel/society/integration.py::_init_persistence) uses
    # REDIS_HOST/REDIS_PORT instead — without this fallback, an
    # environment configured the way this whole system already connects
    # to Redis (host/port, no REDIS_URL) silently gets an in-memory-only
    # RunStore, and every compiled run/execution graph is lost on every
    # restart with no log line indicating it (confirmed live during Gate
    # 6's persistence check: this is exactly the gap TimelineStore's own
    # _make_backend() already fixed — that fix was never propagated here).
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        url = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"

    if choice == "memory":
        logger.info("RunStore: process-local memory backend (RUN_STORE_BACKEND=memory)")
        return _InMemoryRunBackend()

    if choice in ("auto", "redis") and url:
        backend = _RedisRunBackend(url)
        if backend.available():
            logger.info("RunStore: SHARED Redis backend at %s — multi-worker safe", _redact(url))
            return backend
        if choice == "redis":
            logger.error(
                "RunStore: RUN_STORE_BACKEND=redis but Redis at %s is unreachable — falling "
                "back to process-local memory; /learn will be degraded across workers",
                _redact(url),
            )
        else:
            logger.info("RunStore: REDIS_URL set but unreachable — using process-local memory backend")
        return _InMemoryRunBackend()

    return _InMemoryRunBackend()


class RunStore:
    """Singleton run_id -> compiled-run store. Delegates to a process-local (default)
    or shared Redis backend; the public API is identical either way.
    """

    _instance: "RunStore | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "RunStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._backend = _make_backend()
                    cls._instance = instance
        return cls._instance

    def store(
        self,
        run_id: str,
        ir: IntentIR,
        target: str = "execute",
        graph_snapshot: dict[str, Any] | None = None,
    ) -> None:
        if target not in VALID_TARGETS:
            raise ValueError(f"invalid target {target!r} — must be one of {sorted(VALID_TARGETS)}")
        self._backend.store(run_id, ir, target, graph_snapshot)

    def get(self, run_id: str) -> IntentIR | None:
        return self._backend.get(run_id)

    def get_target(self, run_id: str) -> str | None:
        return self._backend.get_target(run_id)

    def get_graph(self, run_id: str) -> dict[str, Any] | None:
        return self._backend.get_graph(run_id)

    def store_graph(self, run_id: str, graph_snapshot: dict[str, Any], target: str | None = None) -> None:
        self._backend.store_graph(run_id, graph_snapshot, target)

    def store_outcome(self, run_id: str, outcome: dict[str, Any]) -> None:
        """Record how a run actually ended (success / failed_steps).

        The execution result travels back to the API as a 4-tuple
        (answer, semantic_hits, graph_paths, llm_answered) that DROPS the success flag the
        workload layer computes — so /execute returned HTTP 200 with llm_answered=true even
        when a step had failed and compensation could not recover it. The failure was only
        visible as "[failed]" buried inside the human-readable answer string. Recording it
        here lets the route surface it without rewriting that tuple contract, which is
        consumed by qa/healing/prompt/simulate helpers too.
        """
        self._backend.store_outcome(run_id, outcome)

    def get_outcome(self, run_id: str) -> dict[str, Any] | None:
        return self._backend.get_outcome(run_id)

    def has(self, run_id: str) -> bool:
        return self._backend.has(run_id)

    def remove(self, run_id: str) -> bool:
        """Drop a run and its outcome. Returns True if the run existed."""
        return self._backend.remove(run_id)

    def latest_graph(self, *, target: str | None = None, graph_type: str | None = None) -> dict[str, Any] | None:
        """The most recently stored graph matching `target` and/or `graph_type`.

        /learn needs the latest EXECUTION graph and the latest SIMULATION graph as two
        distinct sides to diff; it used to walk the store directly and take the first
        snapshot it found for both, which meant diffing a graph against itself. With the
        shared Redis backend this reaches across workers, so /learn on any worker sees the
        execute/simulate graphs produced on the others.
        """
        return self._backend.latest_graph(target=target, graph_type=graph_type)

    def run_ids(self) -> list[str]:
        """All stored run_ids, oldest-first (the order latest_graph/dashboards walk)."""
        return self._backend.run_ids()

    def get_run(self, run_id: str) -> StoredRun | None:
        """The full StoredRun (intent_ir + target + graph_snapshot) for a run_id, or None."""
        return self._backend.get_run(run_id)


def get_run_store() -> RunStore:
    return RunStore()


async def replay(
    run_id: str,
    mongo_client: Any = None,
    *,
    runtime: Any = None,
    sim_runtime: Any = None,
    comparator: Any = None,
    plan_steps: list[dict[str, Any]] | None = None,
) -> Any:
    """Re-run the exact compiled plan stored for `run_id`, dispatching to
    whichever runtime (Cognitive/Simulation/Comparator) originally compiled
    it. Never re-classifies the original question.

    `runtime` — the caller's already-booted runtime. Without it this resolves
    the Kernel-booted singleton via get_cognitive_runtime_instance(), which
    raises if Kernel has not booted rather than returning an unconfigured
    CognitiveRuntime() missing the subsystems boot() injects.
    `plan_steps` — the steps to re-run, derived by the caller from the stored
    execution graph. The executor REFUSES to resolve a workload of its own
    (see GoalExecutor._run_goal), so replaying with no steps could only ever
    fail; this used to pass none, and every /replay returned
    "Error executing goal …: No planner workload was provided".

    Return shape depends on the target: CognitiveRuntime's execute_cognitive_
    workload returns the 4-tuple (answer, semantic_hits, graph_paths,
    llm_answered); SimulationRuntime/ComparatorRuntime return their own result
    dicts (see each class's run()). Callers must branch on target (via
    get_run_store().get_target(run_id)) to know which shape to expect.
    """
    store = get_run_store()
    ir = store.get(run_id)
    target = store.get_target(run_id)
    if ir is None or target is None:
        logger.warning("run=%r replay requested but no stored IntentIR found", run_id)
        return (
            f"Error replaying run {run_id!r}: no stored IntentIR for this run_id",
            [],
            [],
            False,
        )

    if target == "execute":
        from src.monkey_brain.kernel.cognitive_runtime import (
            get_cognitive_runtime_instance,
        )
        from src.monkey_brain.kernel.execute.models import ExecutionMode

        runtime = runtime or get_cognitive_runtime_instance()
        context = runtime.build_execution_runtime(ir, ExecutionMode.EXECUTE, user_id="replay")
        return await runtime.execute_cognitive_workload(
            context,
            mongo_client,
            plan_steps=list(plan_steps or []),
        )

    if target == "simulate":
        from src.monkey_brain.kernel.simulation_runtime import get_simulation_runtime

        return await (sim_runtime or get_simulation_runtime()).run(ir, mongo_client)

    if target == "compare":
        from src.monkey_brain.kernel.cognitive_runtime import (
            get_cognitive_runtime_instance,
        )
        from src.monkey_brain.kernel.execute.models import ExecutionMode
        from src.monkey_brain.kernel.simulation_runtime import get_simulation_runtime
        from src.monkey_brain.kernel.comparator_runtime import get_comparator_runtime

        sim_runtime = sim_runtime or get_simulation_runtime()
        cog_runtime = runtime or get_cognitive_runtime_instance()
        comparator = comparator or get_comparator_runtime()
        sim_result = await sim_runtime.run(ir, mongo_client)
        # Same defect the execute path had: with no plan_steps the executor refuses to
        # resolve a workload and returns "No planner workload was provided" as the
        # ANSWER, so replaying a compare run silently compared against a failure.
        exec_result = await cog_runtime.execute_cognitive_workload(
            cog_runtime.build_execution_runtime(ir, ExecutionMode.EXECUTE, user_id="replay"),
            mongo_client,
            plan_steps=list(plan_steps or []),
        )
        execution_result = {
            "answer": exec_result[0],
            "semantic_hits": exec_result[1],
            "graph_paths": exec_result[2],
            "llm_answered": exec_result[3],
            "state": {"answer": exec_result[0]},
            "operations": [],
            "events": [],
            "artifacts": [],
        }
        return await comparator.compare(sim_result.get("simulation_graph", {}), execution_result)

    if target == "codegen":
        from src.monkey_brain.kernel.codegen_runtime import get_codegen_runtime_instance

        codegen = get_codegen_runtime_instance()
        if codegen is None:
            return (
                f"Error replaying run {run_id!r}: CodeGenRuntime is not booted",
                [],
                [],
                False,
            )
        return await codegen.run(ir, mongo_client)

    return (f"Error replaying run {run_id!r}: unknown target {target!r}", [], [], False)
