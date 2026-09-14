"""Runtime wiring — per-tenant world tensors fed by live executions.

Each tenant gets its own private SparseTransitionTensor.  A shared tensor
holds cross-tenant abstractions.  A tenant's view is `private ∪ shared`.

TenantWorld replaces the old process-global _WORLD singleton:

    get_tenant_world()              → TenantWorld (manages all tenants)
    get_world(tenant_id)            → TenantView (scoped to one tenant)
    get_world()                     → TenantView for "default" tenant (backward compat)

Optionally persisted to MB_WORLD_TENSOR_PATH across restarts.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.compile.tenancy import TenantWorld

logger = logging.getLogger("agentos.compile.world")

_tenant_world: TenantWorld | None = None
_LOCK = threading.Lock()


def enabled() -> bool:
    # Opt-in (default OFF): the world tensor stays inert on the hot execution path unless
    # MB_WORLD_TENSOR is explicitly enabled — backward-compatible and matches flag-gating.
    return os.getenv("MB_WORLD_TENSOR", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_tenant_world() -> TenantWorld:
    """The process-global TenantWorld, loaded from MB_WORLD_TENSOR_PATH if present."""
    global _tenant_world
    if _tenant_world is None:
        with _LOCK:
            if _tenant_world is None:
                _tenant_world = _build_tenant_world()
    return _tenant_world


def _build_tenant_world() -> TenantWorld:
    # Out-of-core mode: an explicit shard_dir always wins — a deliberate,
    # already-established choice (bounded local-disk shards for a single
    # process managing more tenants than fit comfortably in RAM), unrelated
    # to the cross-process concern the Redis backend below addresses.
    shard_dir = os.getenv("AGENTOS_WORLD_SHARD_DIR", "")
    if shard_dir:
        from src.monkey_brain.kernel.compile.sharded_world import ShardedWorldStore

        store = ShardedWorldStore(shard_dir, max_resident=int(os.getenv("AGENTOS_WORLD_MAX_RESIDENT", "128")))
        logger.info(
            "[world] out-of-core sharded world at %s (max_resident=%d)",
            shard_dir,
            store._max,
        )
        return TenantWorld(store=store)

    # Deployment Architecture (Section 7/10/12): the previous unconditional
    # fallback below — one local JSON file, loaded once at boot and
    # last-write-wins saved — is exactly why deploy/k8s/deployment.yaml
    # pins `replicas: 1` and deploy/k8s/pvc.yaml is `ReadWriteOnce`: a
    # second replica either never sees the first's learned transitions, or
    # silently clobbers them. AGENTOS_WORLD_BACKEND mirrors RunStore's own
    # RUN_STORE_BACKEND=auto/redis/memory selection (run_store.py): "auto"
    # (default) prefers the shared, cross-process-safe Redis-backed store
    # whenever Redis is reachable — which every real deployment of this
    # system already requires for RunStore/negotiation/approval/checkpoint
    # persistence and the distributed planetary-cycle lock — and only
    # falls back to the single-file mode when it genuinely isn't.
    backend_choice = os.getenv("AGENTOS_WORLD_BACKEND", "auto").strip().lower()
    if backend_choice in ("auto", "redis"):
        from src.monkey_brain.kernel.compile.redis_world_store import RedisWorldStore

        store = RedisWorldStore.connect(
            max_resident=int(os.getenv("AGENTOS_WORLD_MAX_RESIDENT", "128")),
        )
        if store is not None:
            logger.info("[world] SHARED Redis-backed tenant world — multi-replica safe")
            return TenantWorld(store=store)
        if backend_choice == "redis":
            logger.error(
                "[world] AGENTOS_WORLD_BACKEND=redis but Redis is unreachable — falling back "
                "to process-local single-file mode; a second replica will NOT see this "
                "process's learned transitions",
            )
        else:
            logger.info("[world] Redis unavailable — using process-local single-file mode")

    # Fallback: the original behavior, unchanged — process-local, single
    # JSON file, safe only for a single replica (AGENTOS_WORLD_BACKEND=memory
    # forces this explicitly, e.g. for local/dev use with no Redis at all).
    tw = TenantWorld()
    path = os.getenv("MB_WORLD_TENSOR_PATH", "")
    if path and Path(path).exists():
        try:
            tw.load(path)
            logger.info("[world] loaded tenant world from %s", path)
        except Exception as exc:
            logger.warning("[world] load failed: %s", exc)
    return tw


def get_world_tensor(tenant_id: str = "default") -> SparseTransitionTensor:
    """The tenant's private WRITABLE tensor — for learners, not queriers.

    get_world() hands back a read-only TenantView, which has no intern()/_cells;
    anything that writes Q-values (QTable, via GraphManager) needs the real tensor.
    """
    return get_tenant_world().private_tensor(tenant_id)


def get_world(tenant_id: str = "default") -> "TenantView":
    """Return a tenant-scoped view of the world.

    Backward compatible: get_world() returns the "default" tenant's view.
    """
    tw = get_tenant_world()
    return tw.view(tenant_id)


def observe_execution(
    agents,
    agent_edges,
    *,
    domain: str = "default",
    reward: float = 1.0,
    tenant_id: str = "default",
) -> int:
    """Fold a completed execution's transitions into the tenant's world tensor.

    No-op unless MB_WORLD_TENSOR is enabled. Returns the number of transitions observed
    (0 if disabled). Safe to call from the hot execution path — cheap and defensive.
    """
    if not enabled():
        return 0
    try:
        tw = get_tenant_world()
        n = 0
        for edge in agent_edges or ():
            src, dst = (edge[0], edge[1]) if isinstance(edge, (tuple, list)) and len(edge) >= 2 else (None, None)
            if src and dst:
                tw.observe(tenant_id, str(src), str(dst), domain=domain, reward=reward)
                n += 1
        if n:
            logger.info(
                "[world] observed %d execution transition(s) for tenant %s",
                n,
                tenant_id,
            )
            _maybe_save(tw)
        return n
    except Exception as exc:
        logger.debug("[world] observe_execution skipped: %s", exc)
        return 0


def _maybe_save(tw: TenantWorld) -> None:
    if tw.is_store_backed:
        tw.flush()  # Redis or local-disk-shard store: persist resident tenants, per-tenant
        return
    path = os.getenv("MB_WORLD_TENSOR_PATH", "")
    if path:
        try:
            tw.save(path)
        except Exception as exc:
            logger.debug("[world] save skipped: %s", exc)


def reset_for_test() -> None:
    """Clear the process-global tenant world (tests only)."""
    global _tenant_world
    _tenant_world = None
