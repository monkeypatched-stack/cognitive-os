"""RedisWorldStore — cross-process, per-tenant world-tensor persistence.

Same `store` interface TenantWorld already accepts (get/flush/tenants/
resident_count/evictions — see tenancy.py::TenantWorld._tensor and
sharded_world.py::ShardedWorldStore, the interface's one prior
implementation), so this is a drop-in swap for ShardedWorldStore's
local-disk-shard tier — but network-reachable, closing the specific,
already-self-documented reason deploy/k8s/deployment.yaml pins
`replicas: 1` and deploy/k8s/pvc.yaml is `ReadWriteOnce`: the previous
default (world_tensor.py's single local JSON file, MB_WORLD_TENSOR_PATH)
meant a second replica either never saw the first's learned transitions,
or silently clobbered them on its own next whole-world save — last-write-
wins on ONE key covering every tenant.

Same bounded-LRU-plus-persistence-tier shape as ShardedWorldStore (a
tenant is loaded on first access and evicted to its backing store under
memory pressure); only the tier changes, from local disk to Redis, so
every replica reads/writes the SAME durable per-tenant record and
TenantWorld's own contract is untouched.

Persistence granularity is per-tenant (one Redis key per tenant id), not
the whole-world JSON blob the old file mode wrote on every single
observe_execution() call — a busy world with many tenants no longer pays
an O(all tenants) write for one tenant's new transition, the same lesson
already applied to actor persistence (PlanetaryRuntime._save_actor(), not
_save_actors(), on the hot registration path).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from typing import Any

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor

logger = logging.getLogger("agentos.compile.redis_world_store")

_KEY_PREFIX = "monkeybrain:world_tensor:tenant:"
_TENANTS_SET_KEY = "monkeybrain:world_tensor:tenants"
_LOCK_KEY_PREFIX = "monkeybrain:world_tensor:lock:"
_LOCK_TTL_SECONDS = 30

# Same SET NX EX + Lua compare-and-delete idiom as PlanetaryRuntime's
# planetary-cycle/actor-lease locks (society/integration.py) — a plain
# GET-then-DEL from Python has the same unsafe-release race that idiom
# exists to close (a slow/orphaned holder's delayed release deleting a
# DIFFERENT replica's lock that legitimately acquired the key after this
# one's TTL expired). Duplicated locally rather than imported: compile/
# has no other dependency on society/, and this is a two-line script.
_RELEASE_LOCK_IF_OWNER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


class RedisWorldStore:
    """Per-tenant SparseTransitionTensor persistence, Redis-backed, with a
    bounded in-memory LRU — same shape as ShardedWorldStore, different
    persistence tier."""

    def __init__(self, *, max_resident: int = 128, learning_rate: float = 0.1, discount: float = 0.95) -> None:
        self._max = max(1, max_resident)
        self._lr = learning_rate
        self._discount = discount
        self._resident: OrderedDict[str, SparseTransitionTensor] = OrderedDict()
        self._evictions = 0
        self._client: Any = None
        # revision each tenant's resident tensor was loaded at (or last
        # successfully persisted at) — the baseline _persist() compares
        # Redis's current revision against, to tell "routine" from
        # "another replica structurally changed this since we last saw it".
        self._loaded_revision: dict[str, int] = {}
        # remote revision we've already raised a PendingNegotiation for, per
        # tenant — without this, a resident tenant whose remote is ahead
        # re-raises (and re-persists, with a fresh uuid4 execution_id) on
        # EVERY subsequent flush() until an operator resolves it, since
        # _loaded_revision itself is deliberately never advanced past a
        # conflict (that would mean silently accepting the remote write we
        # just refused to overwrite). Tracked separately so a genuinely NEW
        # remote revision (a second structural change while we're still
        # stuck) still raises again.
        self._conflicted_revision: dict[str, int] = {}

    @classmethod
    def connect(cls, **kwargs: Any) -> RedisWorldStore | None:
        """Construct and verify connectivity in one step. Returns None
        (never raises) if Redis is unreachable, so world_tensor.py's
        backend selection can fall back to another store without its own
        try/except — same "decide once, fail soft" shape as RunStore's
        _make_backend()/_RedisRunBackend.available()."""
        store = cls(**kwargs)
        try:
            import redis  # redis-py; already a declared dependency

            store._client = redis.from_url(
                _redis_url(),
                decode_responses=True,
                socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
                socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
            )
            store._client.ping()
        except Exception as exc:
            logger.warning("RedisWorldStore: Redis unavailable (%s) — caller should fall back", exc)
            return None
        return store

    def _key(self, tenant: str) -> str:
        return f"{_KEY_PREFIX}{tenant}"

    def _fetch_raw(self, tenant: str) -> dict[str, Any] | None:
        """Tenant's raw Redis record, JSON-decoded. None if there is no
        record yet. May raise (connection error, malformed JSON) --
        callers decide how to log/handle failure: get() wants a failure
        here visible at warning level, while _peek_remote_revision()
        (called on every persist()) wants the same failure quiet at
        debug. Single place both go through so a future change to key
        format or decode handling only needs one edit."""
        raw = self._client.get(self._key(tenant))
        if not raw:
            return None
        return json.loads(raw)

    def get(self, tenant: str) -> SparseTransitionTensor:
        """Return a tenant's world, loading it from Redis on first access
        in THIS process (LRU touch) — reflects whatever any process most
        recently flushed for this tenant, not just this process's own
        history. Starts fresh (never raises) if the Redis read fails or
        the tenant has no record yet."""
        t = self._resident.get(tenant)
        if t is not None:
            self._resident.move_to_end(tenant)
            return t
        t = SparseTransitionTensor(self._lr, self._discount)
        try:
            remote = self._fetch_raw(tenant)
            if remote is not None:
                t.load_dict(remote)
        except Exception as exc:
            logger.warning(
                "RedisWorldStore.get(%r) load failed (starting fresh, non-fatal): %s",
                tenant,
                exc,
            )
        self._loaded_revision[tenant] = t.revision
        self._admit(tenant, t)
        return t

    def _admit(self, tenant: str, t: SparseTransitionTensor) -> None:
        self._resident[tenant] = t
        self._resident.move_to_end(tenant)
        while len(self._resident) > self._max:
            old_tenant, old_t = self._resident.popitem(last=False)  # least-recently-used
            self._persist(old_tenant, old_t)
            self._evictions += 1
            logger.info("[redis_world] evicted tenant %s to Redis (resident=%d)", old_tenant, len(self._resident))

    def _persist(self, tenant: str, t: SparseTransitionTensor) -> None:
        """Write a tenant's tensor to Redis — guarded by a per-tenant
        distributed lock (SET NX EX + Lua compare-and-delete, see module
        header) so two replicas' persist() calls for the SAME tenant never
        interleave, and never blind-overwrite a structural change another
        replica made since this resident copy was loaded.

        tensor.py's `revision` only bumps on batch_update() (structural
        world changes — new domains/stores/capabilities), never on
        observe() (routine per-transition accumulation), by design — see
        SparseTransitionTensor.batch_update()'s own docstring. So comparing
        Redis's current revision against self._loaded_revision[tenant]
        detects exactly "did another replica structurally change this
        tenant's world since I last read it", which is the collision this
        store's own module docstring already named as a known gap (two
        replicas resident for the same tenant, last-write-wins on flush).
        Routine concurrent observe() traffic across replicas is NOT
        flagged here — those accumulators are commutative/additive, not a
        semantic conflict — this only guards the structural case.
        """
        token = f"{os.getpid()}:{uuid.uuid4().hex}"
        lock_key = f"{_LOCK_KEY_PREFIX}{tenant}"
        try:
            acquired = bool(self._client.set(lock_key, token, nx=True, ex=_LOCK_TTL_SECONDS))
        except Exception as exc:
            logger.warning(
                "RedisWorldStore: lock acquire failed for tenant %r (%s) — skipping persist "
                "this cycle (non-fatal, will retry next flush)",
                tenant,
                exc,
            )
            return
        if not acquired:
            logger.info(
                "RedisWorldStore: tenant %r locked by another replica — skipping persist "
                "this cycle (will retry next flush)",
                tenant,
            )
            return
        try:
            remote_revision = self._peek_remote_revision(tenant)
            loaded_revision = self._loaded_revision.get(tenant, 0)
            if remote_revision > loaded_revision:
                if remote_revision != self._conflicted_revision.get(tenant):
                    self._raise_conflict(tenant, t, loaded_revision, remote_revision)
                    self._conflicted_revision[tenant] = remote_revision
                return
            self._client.set(self._key(tenant), json.dumps(t.to_dict(), default=str))
            self._client.sadd(_TENANTS_SET_KEY, tenant)
            self._loaded_revision[tenant] = t.revision
        except Exception as exc:
            logger.warning("RedisWorldStore: persist failed for tenant %r (non-fatal): %s", tenant, exc)
        finally:
            try:
                self._client.eval(_RELEASE_LOCK_IF_OWNER_SCRIPT, 1, lock_key, token)
            except Exception as exc:
                logger.debug(
                    "RedisWorldStore: lock release failed for tenant %r (TTL will expire it): %s",
                    tenant,
                    exc,
                )

    def _peek_remote_revision(self, tenant: str) -> int:
        """Redis's currently-stored revision for tenant, without disturbing
        this store's own resident/LRU state. 0 (never conflicts) if the
        tenant has no record yet or the read fails."""
        try:
            remote = self._fetch_raw(tenant)
            return int(remote.get("revision", 0)) if remote is not None else 0
        except Exception as exc:
            logger.debug("RedisWorldStore: revision peek failed for tenant %r (%s) — assuming 0", tenant, exc)
            return 0

    def _raise_conflict(
        self,
        tenant: str,
        t: SparseTransitionTensor,
        loaded_revision: int,
        remote_revision: int,
    ) -> None:
        """Another replica structurally advanced this tenant past what we
        loaded — refuse to overwrite it, and record the conflict as a
        PendingNegotiation (negotiation_store.py) instead of guessing which
        side wins. An operator/reconciliation job resolves it via
        resolve_world_tensor_conflict(), below. This replica's local
        snapshot is attached in full so nothing is lost even though it
        isn't applied automatically."""
        from src.monkey_brain.kernel.pipeline.negotiation_store import PendingNegotiation, save_pending_negotiation

        execution_id = f"world_tensor:{tenant}:{int(time.time())}:{uuid.uuid4().hex[:8]}"
        negotiation = PendingNegotiation(
            execution_id=execution_id,
            capability="world_tensor.persist",
            reason=(
                f"tenant {tenant!r} world tensor: remote revision {remote_revision} has "
                f"advanced past the revision {loaded_revision} this replica last loaded — "
                "another replica applied a structural change (batch_update) since. Refusing "
                "to overwrite; local unpersisted snapshot attached for a decision."
            ),
            proposed_transition={
                "tenant_id": tenant,
                "local_revision": loaded_revision,
                "remote_revision": remote_revision,
                "local_snapshot": t.to_dict(),
            },
        )
        if save_pending_negotiation(negotiation):
            logger.warning(
                "RedisWorldStore: world-tensor collision for tenant %r (local rev %d vs remote "
                "rev %d) — recorded as pending negotiation %r instead of overwriting",
                tenant,
                loaded_revision,
                remote_revision,
                execution_id,
            )
        else:
            logger.error(
                "RedisWorldStore: world-tensor collision for tenant %r (local rev %d vs remote "
                "rev %d) AND the negotiation record could not be saved — local changes since "
                "rev %d are being dropped this cycle (will surface again next flush)",
                tenant,
                loaded_revision,
                remote_revision,
                loaded_revision,
            )

    def save(self, tenant: str) -> None:
        t = self._resident.get(tenant)
        if t is not None:
            self._persist(tenant, t)

    def flush(self) -> None:
        """Persist all currently-resident tenants to Redis — called after
        every observe_execution() (world_tensor.py::_maybe_save), same
        contract as ShardedWorldStore.flush()."""
        for tenant, t in self._resident.items():
            self._persist(tenant, t)

    def resident_count(self) -> int:
        return len(self._resident)

    def evictions(self) -> int:
        return self._evictions

    def tenants(self) -> list[str]:
        """All known tenants: resident locally, plus every tenant any
        process has ever flushed to Redis."""
        try:
            known = set(self._client.smembers(_TENANTS_SET_KEY))
        except Exception as exc:
            logger.warning("RedisWorldStore.tenants() Redis read failed: %s", exc)
            known = set()
        return sorted(known | set(self._resident))


def resolve_world_tensor_conflict(execution_id: str, *, accept_local: bool) -> bool:
    """Apply an operator's decision on a pending world_tensor.persist
    negotiation (see RedisWorldStore._raise_conflict).

    accept_local=True force-writes the conflicting replica's attached
    snapshot, overwriting whatever is in Redis now. accept_local=False
    discards it, keeping the current remote state as authoritative. Either
    way marks the negotiation decided. Standalone (opens its own Redis
    connection) rather than a RedisWorldStore method, since the process
    resolving a conflict — an operator route, a reconciliation job — is
    typically not the replica that raised it and has no resident copy."""
    from src.monkey_brain.kernel.pipeline.negotiation_store import (
        load_pending_negotiation,
        resolve_pending_negotiation,
    )

    negotiation = load_pending_negotiation(execution_id)
    if negotiation is None:
        return False
    if accept_local:
        tenant = negotiation.proposed_transition.get("tenant_id", "")
        snapshot = negotiation.proposed_transition.get("local_snapshot")
        if not tenant or not snapshot:
            return False
        try:
            import redis

            client = redis.from_url(_redis_url(), decode_responses=True)
            client.set(f"{_KEY_PREFIX}{tenant}", json.dumps(snapshot, default=str))
            client.sadd(_TENANTS_SET_KEY, tenant)
        except Exception as exc:
            logger.error(
                "resolve_world_tensor_conflict(%s): force-write of local snapshot failed: %s",
                execution_id,
                exc,
            )
            return False
    resolve_pending_negotiation(execution_id, accepted=accept_local)
    return True
