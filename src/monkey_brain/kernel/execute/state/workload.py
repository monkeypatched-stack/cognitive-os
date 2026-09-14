"""ImmutableStateWorkload — two-tier context manager for the reducer log.

Active tier  — fast in-process dict; holds nodes younger than their TTL.
               Proportional to recent, operationally relevant state.

Backup tier  — durable store; holds evicted nodes in full, unaltered form.
               Reconstructable on demand for audit, replay, or debugging.

Eviction moves records between tiers.  No record is ever deleted.

    "Querying what is true right now" → fast read against the active tier.
    "How did we get here"             → slower read against the backed-up log.
    Neither query is served by data that no longer exists.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.monkey_brain.kernel.execute.state.node import StateNode
from src.monkey_brain.kernel.execute.state.reducer import ReducerRegistry

logger = logging.getLogger("monkey_brain.state.workload")

_DEFAULT_TTL = 300.0


# ── Backup store interface ────────────────────────────────────────────────────


class BackupStore(Protocol):
    """Pluggable durable tier for evicted StateNodes."""

    def write(self, node: StateNode) -> None: ...
    def read(self, node_id: str) -> StateNode | None: ...
    def read_all(self) -> list[StateNode]: ...


class InMemoryBackupStore:
    """Default in-process backup.  Swap for a graph DB or object store in prod."""

    def __init__(self) -> None:
        self._store: dict[str, StateNode] = {}

    def write(self, node: StateNode) -> None:
        self._store[node.node_id] = node

    def read(self, node_id: str) -> StateNode | None:
        return self._store.get(node_id)

    def read_all(self) -> list[StateNode]:
        return sorted(self._store.values(), key=lambda n: n.created_at)

    def size(self) -> int:
        return len(self._store)


# ── Eviction result ───────────────────────────────────────────────────────────


@dataclass
class EvictionResult:
    evicted: int = 0
    backup_size: int = 0
    swept_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evicted": self.evicted,
            "backup_size": self.backup_size,
            "swept_at": self.swept_at,
        }


# ── Workload ──────────────────────────────────────────────────────────────────


class ImmutableStateWorkload:
    """Orchestrates the reducer chain and the active ↔ backup tier boundary.

    Each apply() is a pure write — current_head + action → new immutable StateNode
    appended to the active tier.  The active tier never exceeds TTL-bounded size.

    Each evict_stale() is a pure tier migration — expired active nodes are moved
    to the backup store unaltered and removed from the active dict.  The head node
    is protected from eviction regardless of age.

    reconstruct() walks the parent_id chain across both tiers to answer lineage
    queries without requiring any ancestor node to still be active.
    """

    def __init__(
        self,
        reducers: ReducerRegistry,
        backup_store: BackupStore | None = None,
        default_ttl: float = _DEFAULT_TTL,
    ) -> None:
        self._reducers = reducers
        self._backup = backup_store or InMemoryBackupStore()
        self._default_ttl = default_ttl

        self._active: dict[str, StateNode] = {}
        self._head_id: str | None = None

    # ── Write path ────────────────────────────────────────────────────────────

    def bootstrap(
        self,
        initial_state: dict[str, Any],
        action_type: str = "INIT",
        ttl: float | None = None,
    ) -> StateNode:
        """Seed the workload with an initial state node (no parent)."""
        node = StateNode(
            parent_id=None,
            action_type=action_type,
            state=initial_state,
            ttl=ttl if ttl is not None else self._default_ttl,
        )
        self._active[node.node_id] = node
        self._head_id = node.node_id
        logger.debug("[state] bootstrap → %s", node.node_id)
        return node

    def apply(
        self,
        action_type: str,
        payload: dict[str, Any],
        ttl: float | None = None,
    ) -> StateNode:
        """Apply a reducer and append the resulting node to the active tier.

        The reducer is pure — it receives only the current state dict and
        the payload.  StateNode construction (parent wiring, checksum, TTL)
        is handled by the ReducerRegistry, not the reducer itself.
        """
        current = self.head()
        new_node = self._reducers.dispatch(
            current,
            action_type,
            payload,
            ttl=ttl if ttl is not None else self._default_ttl,
        )
        self._active[new_node.node_id] = new_node
        self._head_id = new_node.node_id
        logger.debug(
            "[state] applied %s → node %s (parent=%s)",
            action_type,
            new_node.node_id,
            new_node.parent_id,
        )
        return new_node

    # ── Read path ─────────────────────────────────────────────────────────────

    def head(self) -> StateNode | None:
        """Return the most recently written active node."""
        return self._active.get(self._head_id) if self._head_id else None

    def query_active(self) -> list[StateNode]:
        """All nodes in the active tier, chronological order."""
        return sorted(self._active.values(), key=lambda n: n.created_at)

    # ── Eviction ──────────────────────────────────────────────────────────────

    def evict_stale(self) -> EvictionResult:
        """Move expired active nodes to the backup tier.

        The head node is never evicted — it always remains reachable in the
        active tier as the authoritative current state.
        """
        result = EvictionResult()
        expired = [nid for nid, node in self._active.items() if node.is_expired and nid != self._head_id]
        for nid in expired:
            node = self._active.pop(nid)
            self._backup.write(node)
            result.evicted += 1
            logger.debug("[state] evicted node %s → backup", nid)

        result.backup_size = getattr(self._backup, "size", lambda: -1)()
        if result.evicted:
            logger.info("[state] eviction sweep: %d nodes migrated to backup", result.evicted)
        return result

    # ── Reconstruction ────────────────────────────────────────────────────────

    def reconstruct(self, node_id: str) -> list[StateNode]:
        """Walk the parent_id chain from node_id back to the root.

        Searches the active tier first, then the backup store.  Returns the
        full chain in chronological order (oldest first).  If a link in the
        chain is missing from both tiers, reconstruction stops at that point
        and logs a warning rather than raising.
        """
        chain: list[StateNode] = []
        current_id: str | None = node_id

        while current_id:
            node = self._active.get(current_id) or self._backup.read(current_id)
            if not node:
                logger.warning("[state] reconstruct: node %s not found in either tier", current_id)
                break
            chain.append(node)
            current_id = node.parent_id

        chain.reverse()
        return chain

    # ── Introspection ─────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        head = self.head()
        return {
            "active_nodes": len(self._active),
            "head_id": self._head_id,
            "head_action": head.action_type if head else None,
            "head_age": round(head.age, 2) if head else None,
            "backup_size": getattr(self._backup, "size", lambda: -1)(),
            "default_ttl": self._default_ttl,
        }
