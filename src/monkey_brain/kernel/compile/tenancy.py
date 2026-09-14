"""Multitenancy as a first-class dimension (ADR 0021).

Tenancy is the top-level index into the world. Each tenant has its OWN private world
tensor (its private learned edge weights), plus a SHARED tensor holding cross-tenant
abstractions everyone may see. A tenant's view is `private ∪ shared` and never includes
another tenant's private transitions.

The flow is  token claim → Context → query filter:

    claims  ──Context.from_token──►  Context(tenant_id)  ──TenantWorld.view──►  TenantView
                                                                                    │
                                             every world query goes through here ◄──┘
                                             (successors / feature / iter) — scoped, isolated

This is also the federation boundary: private per-tenant weights, shared higher-level
abstractions — federated learning by construction.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.context import Context
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor

logger = logging.getLogger("agentos.compile.tenancy")

SHARED = "_shared"


class TenantView:
    """A read view scoped to ONE tenant = its private tensor ∪ the shared tensor.

    Implements the read interface the actor cognitive cycle uses (successors / feature /
    domain_of / iter), so isolation is enforced by construction: a view built for tenant
    A can never surface tenant B's private transitions.
    """

    def __init__(
        self,
        private: SparseTransitionTensor,
        shared: SparseTransitionTensor,
        tenant_id: str,
    ) -> None:
        self._private = private
        self._shared = shared
        self.tenant_id = tenant_id

    def successors(self, state: str) -> list[tuple[str, str]]:
        seen = set()
        out: list[tuple[str, str]] = []
        for dst, dom in self._private.successors(state) + self._shared.successors(state):
            if (dst, dom) not in seen:
                seen.add((dst, dom))
                out.append((dst, dom))
        return out

    def feature(self, src: str, dst: str, f: Feature) -> float:
        # private overrides shared where the tenant has learned its own value.
        # has_edge is O(1); this used to be `(src, dst) in set(self._private)`, which
        # built a set of EVERY private edge on every read whose value happened to be
        # 0.0 (the common case for REWARD — observe() defaults reward=0). Read cost
        # scaled with the size of the whole world: ~500µs per single read at 8k edges,
        # on the actor's hot path.
        v = self._private.feature(src, dst, f)
        if v != 0.0 or self._private.has_edge(src, dst):
            return v
        return self._shared.feature(src, dst, f)

    def domain_of(self, state: str) -> str:
        d = self._private.domain_of(state)
        return d if d != "default" else self._shared.domain_of(state)

    def nnz(self) -> int:
        return len(set(self._private) | set(self._shared))

    def __iter__(self):
        seen = set()
        for edge in list(self._private) + list(self._shared):
            if edge not in seen:
                seen.add(edge)
                yield edge


class TenantWorld:
    """The multitenant world: tenant → private tensor, plus one shared tensor."""

    def __init__(self, store: "Any" = None) -> None:
        self._tenants: dict[str, SparseTransitionTensor] = {}
        self._shared = SparseTransitionTensor()
        # Optional out-of-core backend: when set, per-tenant tensors live in disk shards
        # with a bounded in-memory LRU, so memory is bounded independent of tenant count.
        self._store = store

    def _tensor(self, tenant_id: str) -> SparseTransitionTensor:
        if self._store is not None:
            return self._store.get(tenant_id)  # lazy-load + LRU (out-of-core)
        t = self._tenants.get(tenant_id)
        if t is None:
            t = SparseTransitionTensor()
            self._tenants[tenant_id] = t
            logger.info("[tenancy] provisioned world for tenant %r", tenant_id)
        return t

    def flush(self) -> None:
        """Persist resident tenant shards (out-of-core mode only)."""
        if self._store is not None:
            self._store.flush()

    @property
    def is_store_backed(self) -> bool:
        """True when tensors are persisted via an external store (Redis or
        local-disk shards) rather than kept only in this process's memory.
        world_tensor.py's _maybe_save() uses this to decide whether to
        flush() the store (per-tenant, external, cross-process-visible)
        instead of writing a single whole-world JSON file."""
        return self._store is not None

    def observe(self, tenant_id: str, src: str, dst: str, *, shared: bool = False, **kw) -> None:
        """Write a transition into a tenant's PRIVATE world (or the shared one). A tenant
        can only ever write its own private world."""
        target = self._shared if shared else self._tensor(tenant_id)
        target.observe(src, dst, **kw)

    def batch_update(self, tenant_id: str, transitions, *, shared: bool = False, source: str = "") -> int:
        target = self._shared if shared else self._tensor(tenant_id)
        return target.batch_update(transitions, source=source or f"tenant:{tenant_id}")

    def private_tensor(self, tenant_id: str) -> SparseTransitionTensor:
        """The tenant's OWN writable tensor — for components that learn (write Q-values)
        rather than query.

        `view()` returns a read-only TenantView; a writer handed one blows up on the
        first `intern()`/`_cells` access (QTable does exactly that). Isolation still
        holds by construction: this is the caller's private tensor, never the shared one
        and never another tenant's.
        """
        return self._tensor(tenant_id)

    def view(self, ctx_or_tenant) -> TenantView:
        """query filter: return the read view scoped to a Context's tenant (or a tenant
        id). All downstream queries go through this — isolation by construction."""
        tenant_id = ctx_or_tenant.tenant_id if isinstance(ctx_or_tenant, Context) else str(ctx_or_tenant)
        _obs.counter("tenancy.view", tenant=tenant_id or "_none")
        return TenantView(self._tensor(tenant_id), self._shared, tenant_id)

    def promote_to_shared(self, tenant_id: str, src: str, dst: str, **kw) -> None:
        """Publish a tenant's transition as a shared abstraction (federation: share the
        higher-level structure, keep private weights local)."""
        self._shared.observe(src, dst, **kw)

    def share_domain(self, tenant_id: str, domain: str, *, ontology_only: bool = False) -> int:
        """Policy-governed selective sharing: publish ONLY the named domain's transitions
        as shared abstractions (e.g. share 'calendar' but not 'journal'). With
        `ontology_only`, share the structure (topology) but not the personal reward/value
        features. Returns the number of transitions shared."""
        t = self._tenant_if_present(tenant_id)
        if t is None:
            return 0
        count = 0
        for src, dst in list(t):
            # BOTH endpoints must be in the shared domain. Filtering on the source
            # alone published every edge LEAVING the domain — so sharing "calendar"
            # while withholding "journal" still leaked the private journal state
            # ("meeting" → "therapy_note") into the shared tensor, where any other
            # tenant could read it. Worse, observe(domain=domain) applies to the
            # destination too, so the journal state was re-labelled as calendar and
            # its true domain erased. An edge that leaves the domain is exactly the
            # edge selective sharing exists to withhold.
            if t.domain_of(src) != domain or t.domain_of(dst) != domain:
                continue
            if ontology_only:
                self._shared.observe(src, dst, domain=domain)  # structure only
            else:
                self._shared.observe(src, dst, domain=domain, reward=t.feature(src, dst, Feature.REWARD))
            count += 1
        return count

    def _tenant_if_present(self, tenant_id: str) -> "SparseTransitionTensor | None":
        """The tenant's tensor if it already exists, else None — never provisions one.

        Callers used `self._tenants.get(...)` directly, which is ALWAYS empty in
        out-of-core mode (tenants live in the shard store), so anything reading it
        silently behaved as if the tenant did not exist.
        """
        if self._store is not None:
            return self._store.get(tenant_id) if tenant_id in set(self._store.tenants()) else None
        return self._tenants.get(tenant_id)

    def tenants(self) -> list[str]:
        if self._store is not None:
            return self._store.tenants()
        return sorted(self._tenants)

    def summary(self) -> dict:
        if self._store is not None:
            return {
                "tenants": len(self._store.tenants()),
                "resident": self._store.resident_count(),
                "evictions": self._store.evictions(),
                "shared_transitions": self._shared.nnz(),
            }
        return {
            "tenants": len(self._tenants),
            "shared_transitions": self._shared.nnz(),
            "private_transitions": {t: w.nnz() for t, w in sorted(self._tenants.items())},
        }

    def to_dict(self) -> dict:
        """Serialize the entire tenant world for persistence.

        In out-of-core mode the tenants live in the shard store and `self._tenants` is
        empty — this used to serialize it anyway, so save() wrote {"tenants": {}} and
        silently dropped EVERY tenant's private world. Go through the store.
        """
        if self._store is not None:
            self._store.flush()  # resident tenants -> their shards, so nothing is lost
            return {
                "shared": self._shared.to_dict(),
                "tenants": {t: self._store.get(t).to_dict() for t in self._store.tenants()},
            }
        return {
            "shared": self._shared.to_dict(),
            "tenants": {t: w.to_dict() for t, w in self._tenants.items()},
        }

    def load(self, path: str) -> None:
        """Load tenant world from a JSON file."""
        import json
        from pathlib import Path

        data = json.loads(Path(path).read_text())
        if "shared" in data:
            self._shared.load_dict(data["shared"])
        for tenant_id, tensor_data in data.get("tenants", {}).items():
            t = self._tensor(tenant_id)
            t.load_dict(tensor_data)

    def save(self, path: str) -> None:
        """Save tenant world to a JSON file."""
        import json
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), default=str))
