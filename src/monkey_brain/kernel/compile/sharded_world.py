"""ShardedWorldStore — out-of-core tenant worlds (ADR 0021).

At planetary scale the world cannot be one process's dict or one JSON blob. Each tenant's
transition tensor is an independent SHARD on disk; only a bounded LRU of tenants is resident
in memory, loaded on demand and persisted on eviction. Memory is therefore bounded by
`max_resident`, independent of how many tenants exist — the shape a real out-of-core /
object-store-backed world takes (swap file shards for S3/KV keys and the logic is identical).

Tenant ids are encoded to safe filenames (no path traversal), so a hostile tenant id like
"../../etc/passwd" can never escape the shard directory.
"""

from __future__ import annotations

import base64
import logging
from collections import OrderedDict
from pathlib import Path

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor

logger = logging.getLogger("agentos.compile.sharded_world")


def _shard_name(tenant: str) -> str:
    """Reversible, traversal-safe filename for a tenant id."""
    return base64.urlsafe_b64encode(tenant.encode("utf-8")).decode("ascii")


def _tenant_of(shard_name: str) -> str:
    return base64.urlsafe_b64decode(shard_name.encode("ascii")).decode("utf-8")


class ShardedWorldStore:
    """Per-tenant tensor shards with a bounded in-memory LRU."""

    def __init__(
        self,
        shard_dir: str | Path,
        *,
        max_resident: int = 128,
        learning_rate: float = 0.1,
        discount: float = 0.95,
    ) -> None:
        self._dir = Path(shard_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max = max(1, max_resident)
        self._lr = learning_rate
        self._discount = discount
        self._resident: "OrderedDict[str, SparseTransitionTensor]" = OrderedDict()
        self._evictions = 0

    def _shard_path(self, tenant: str) -> Path:
        return self._dir / f"{_shard_name(tenant)}.json"

    def get(self, tenant: str) -> SparseTransitionTensor:
        """Return a tenant's world, loading its shard on first access (LRU touch)."""
        t = self._resident.get(tenant)
        if t is not None:
            self._resident.move_to_end(tenant)
            return t
        t = SparseTransitionTensor(self._lr, self._discount)
        p = self._shard_path(tenant)
        if p.exists():
            t.load(p)
        self._admit(tenant, t)
        return t

    def _admit(self, tenant: str, t: SparseTransitionTensor) -> None:
        self._resident[tenant] = t
        self._resident.move_to_end(tenant)
        while len(self._resident) > self._max:
            old_tenant, old_t = self._resident.popitem(last=False)  # least-recently-used
            old_t.save(self._shard_path(old_tenant))  # persist before evicting
            self._evictions += 1
            logger.info(
                "[sharded] evicted tenant %s to shard (resident=%d)",
                old_tenant,
                len(self._resident),
            )

    def save(self, tenant: str) -> None:
        t = self._resident.get(tenant)
        if t is not None:
            t.save(self._shard_path(tenant))

    def flush(self) -> None:
        """Persist all resident tenants to their shards."""
        for tenant, t in self._resident.items():
            t.save(self._shard_path(tenant))

    def resident_count(self) -> int:
        return len(self._resident)

    def evictions(self) -> int:
        return self._evictions

    def tenants(self) -> list[str]:
        """All known tenants (resident + on disk)."""
        on_disk = set()
        for p in self._dir.glob("*.json"):
            try:
                on_disk.add(_tenant_of(p.stem))
            except Exception:
                continue
        return sorted(on_disk | set(self._resident))
