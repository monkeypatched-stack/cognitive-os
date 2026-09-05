"""Synchronization between the control plane and an EdgeLocalStore.

Idempotent by construction: `EdgeLocalStore.put` is an upsert keyed by
(namespace, key), so replaying the same snapshot twice, or receiving
updates out of order, never corrupts state -- `_should_apply` below still
compares epochs explicitly so an OUT-OF-ORDER older update can never
clobber a newer one that already landed, even though the underlying
store write itself would technically succeed either way.

Deliberately a function-call-level integration in this pass, not a live
HTTP/NATS client: `ControlPlaneSyncSource` is the seam a real network
transport plugs into later without touching EdgeSyncClient's own logic
(reconciliation, epoch comparison, revocation application) at all. See
the module docstring's "Known limitation" callout at the bottom.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from src.monkey_brain.kernel.edge.freshness import CacheProvenance
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, SignedPolicySnapshot

logger = logging.getLogger("agentos.edge.sync")

POLICY_STREAM = "policy"
WORLD_PROJECTION_STREAM = "world_projection"


@dataclass(frozen=True)
class SyncResult:
    stream: str
    applied: int
    skipped_stale: int
    new_epoch: int
    full_snapshot: bool
    synced_at: float = field(default_factory=time.time)


class ControlPlaneSyncSource(Protocol):
    """What a real transport (HTTP/NATS) must provide. Kept minimal and
    already-shaped like this codebase's other epoch/cursor sync
    conventions (kernel/society/integration.py's own Redis-registry
    reconciliation) rather than inventing a new one."""

    def current_epoch(self) -> int: ...

    def fetch_snapshots(self, *, since_epoch: int) -> list[SignedPolicySnapshot]:
        """All policy snapshots issued at or after since_epoch. since_epoch=0
        means "everything" (initial sync)."""
        ...

    def fetch_world_projection(self, *, keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        """Authoritative values for the given world-state projection
        keys, each shaped {"value": ..., "version": ..., "expires_at": ...}."""
        ...


class EdgeSyncClient:
    def __init__(self, store: EdgeLocalStore, policy_cache: EdgePolicyCache, source: ControlPlaneSyncSource) -> None:
        self._store = store
        self._policy_cache = policy_cache
        self._source = source

    def current_local_epoch(self) -> int:
        epoch, _, _ = self._store.get_sync_state(POLICY_STREAM)
        return epoch

    def sync_policy(self) -> SyncResult:
        """Initial sync when nothing has ever been synced (last_epoch=0
        and no cursor); incremental otherwise. Both paths funnel through
        the same apply logic, so "initial" is simply "incremental since
        epoch 0" -- no separate code path to keep consistent."""
        last_epoch, _, _ = self._store.get_sync_state(POLICY_STREAM)
        is_initial = last_epoch == 0
        snapshots = self._source.fetch_snapshots(since_epoch=last_epoch)
        current_epoch = self._source.current_epoch()

        applied = 0
        skipped = 0
        for snapshot in snapshots:
            if self._should_apply(snapshot):
                self._policy_cache.store_snapshot(snapshot)
                applied += 1
            else:
                skipped += 1

        self._store.set_sync_state(POLICY_STREAM, last_epoch=current_epoch)
        logger.info(
            "edge sync: policy stream applied=%d skipped_stale=%d new_epoch=%d initial=%s",
            applied, skipped, current_epoch, is_initial,
        )
        return SyncResult(
            stream=POLICY_STREAM, applied=applied, skipped_stale=skipped,
            new_epoch=current_epoch, full_snapshot=is_initial,
        )

    def _should_apply(self, snapshot: SignedPolicySnapshot) -> bool:
        """Out-of-order guard: never let an older-epoch snapshot
        overwrite one that's already cached for the same key, even if
        the transport delivered them out of order."""
        existing = self._store.get(
            "policy_snapshot", f"{snapshot.principal}:{snapshot.action}:{snapshot.resource}",
        )
        if existing is None:
            return True
        return snapshot.authority_epoch >= existing.provenance.authority_epoch

    def sync_world_projection(self, keys: tuple[str, ...], *, freshness_requirement: str = "requires_world_state") -> SyncResult:
        values = self._source.fetch_world_projection(keys=keys)
        applied = 0
        now = time.time()
        for key, payload in values.items():
            provenance = CacheProvenance(
                source="control_plane:world_projection",
                version=str(payload.get("version", "")),
                observed_at=now,
                expires_at=payload.get("expires_at"),
                freshness_requirement=freshness_requirement,
            )
            self._store.put("world_projection", key, {"value": payload.get("value")}, provenance)
            applied += 1
        self._store.set_sync_state(WORLD_PROJECTION_STREAM, last_epoch=self.current_local_epoch())
        return SyncResult(
            stream=WORLD_PROJECTION_STREAM, applied=applied, skipped_stale=0,
            new_epoch=self.current_local_epoch(), full_snapshot=False,
        )

    def reconcile_after_partition(self) -> SyncResult:
        """The actor reconnects after an arbitrarily long disconnection
        and must not assume anything it cached is still authoritative
        beyond what freshness.classify_freshness already enforces per
        entry -- this simply re-runs the normal incremental sync (safe
        regardless of how long the gap was, since it's since_epoch-based,
        not time-based) and lets already-in-place freshness/epoch checks
        naturally invalidate anything superseded while disconnected."""
        return self.sync_policy()


def acknowledge_sync(source: ControlPlaneSyncSource, result: SyncResult) -> None:
    """Best-effort acknowledgement hook -- a real transport would report
    (stream, new_epoch) back to the control plane so it can track which
    edge nodes have confirmed a given epoch (useful for revocation
    propagation monitoring); no-op today since ControlPlaneSyncSource has
    no ack method yet (Known limitation, see module docstring)."""
    logger.debug("edge sync ack (no-op transport): stream=%s epoch=%d", result.stream, result.new_epoch)


# Known limitation: ControlPlaneSyncSource is consulted via direct Python
# calls in this pass (an in-process control-plane implementation would
# satisfy the Protocol directly); a real edge deployment needs an actual
# network-backed implementation (HTTP polling or a NATS subscription)
# behind the SAME Protocol -- EdgeSyncClient's own logic does not change
# either way.
