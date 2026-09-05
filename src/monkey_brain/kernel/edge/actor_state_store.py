"""Edge-local ActorStateStore implementation (Architecture Boundary
Hardening, Section 1) -- backed by kernel/edge/local_store.py::
EdgeLocalStore (SQLite), satisfying the exact same
kernel/pipeline/protocols.py::ActorStateStoreProtocol surface
persistence/actor_state_store.py::ActorStateStore (MongoDB) already
implements, so an actor's belief checkpoint is portable between a cloud
node (Mongo-backed) and an edge/device/robot node (SQLite-backed) without
either the runtime or the cognitive loop knowing which one it is talking
to.

Not a second source of truth: this store holds whatever the LOCAL node
last checkpointed. When an edge node is connected, the control plane's
Mongo-backed ActorStateStore remains the durable, cross-node record of
truth; this class exists so a genuinely offline edge/device/robot node
can still checkpoint and restore an actor's belief across its own
process restarts (Section 7's "the edge must never manufacture
authority" applies equally here: this store never manufactures actor
state that was never actually checkpointed locally, and it never claims
to be the multi-node-authoritative record).
"""
from __future__ import annotations

import time

from src.monkey_brain.kernel.edge.freshness import CacheProvenance
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.persistence.actor_state_store import PersistedActorState

_NAMESPACE = "actor_state"


def _key(actor_id: str, tenant_id: str) -> str:
    return f"{tenant_id}:{actor_id}"


class EdgeActorStateStore:
    """Satisfies kernel/pipeline/protocols.py::ActorStateStoreProtocol.
    JSON-serializes PersistedActorState's bytes fields as base64 text
    (matching persistence/actor_state_store.py::ActorStateStore's own
    on-the-wire encoding convention) so a checkpoint written by one
    implementation is byte-for-byte reconstructable if ever migrated to
    the other."""

    def __init__(self, store: EdgeLocalStore) -> None:
        self._store = store

    def save(self, actor_state: PersistedActorState) -> None:
        import base64

        payload = {
            "actor_id": actor_state.actor_id,
            "tenant_id": actor_state.tenant_id,
            "belief_state": base64.b64encode(actor_state.belief_state).decode(),
            "bellman_policy": base64.b64encode(actor_state.bellman_policy).decode(),
            "phi_compiled": base64.b64encode(actor_state.phi_compiled).decode(),
            "memory_kv": actor_state.memory_kv,
            "world_snapshot": base64.b64encode(actor_state.world_snapshot).decode(),
            "world_version": actor_state.world_version,
            "last_updated": actor_state.last_updated,
            "version": actor_state.version,
            "is_active": actor_state.is_active,
            "cycle_count": actor_state.cycle_count,
            "last_cycle": actor_state.last_cycle,
            "last_model_provider": actor_state.last_model_provider,
            "last_model_name": actor_state.last_model_name,
        }
        provenance = CacheProvenance(
            source="edge_local:actor_state", observed_at=time.time(),
            freshness_requirement="safe_offline",
        )
        self._store.put(_NAMESPACE, _key(actor_state.actor_id, actor_state.tenant_id), payload, provenance)

    def load(self, actor_id: str, tenant_id: str) -> PersistedActorState | None:
        import base64

        entry = self._store.get(_NAMESPACE, _key(actor_id, tenant_id))
        if entry is None:
            return None
        doc = entry.value
        return PersistedActorState(
            actor_id=doc["actor_id"], tenant_id=doc["tenant_id"],
            belief_state=base64.b64decode(doc["belief_state"]),
            bellman_policy=base64.b64decode(doc["bellman_policy"]),
            phi_compiled=base64.b64decode(doc["phi_compiled"]),
            memory_kv=doc.get("memory_kv", {}),
            world_snapshot=base64.b64decode(doc.get("world_snapshot", "")),
            world_version=doc.get("world_version", 0),
            last_updated=doc["last_updated"], version=doc["version"],
            is_active=doc.get("is_active", True), cycle_count=doc.get("cycle_count", 0),
            last_cycle=doc.get("last_cycle", 0.0),
            last_model_provider=doc.get("last_model_provider", ""),
            last_model_name=doc.get("last_model_name", ""),
        )

    def delete(self, actor_id: str, tenant_id: str) -> bool:
        existing = self._store.get(_NAMESPACE, _key(actor_id, tenant_id))
        if existing is None:
            return False
        self._store.delete(_NAMESPACE, _key(actor_id, tenant_id))
        return True

    def list_actors(self, tenant_id: str, active_only: bool = True) -> list[str]:
        prefix = f"{tenant_id}:"
        out = []
        for entry in self._store.list(_NAMESPACE):
            if not entry.key.startswith(prefix):
                continue
            if active_only and not entry.value.get("is_active", True):
                continue
            out.append(entry.value["actor_id"])
        return out
