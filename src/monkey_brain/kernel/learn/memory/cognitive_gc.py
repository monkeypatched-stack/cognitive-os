"""CognitiveGC — hermetic garbage collector for the episodic memory store.

Design invariant: the regulatory provenance chain is NEVER deleted.
Stale episodic nodes are tombstoned — their payload and vector embedding are
removed to free storage, but a minimal GC_TOMBSTONE record is written back
with the original auth_hash so the audit trail remains intact and traversable.

Separation from DLM's GarbageCollector:
  dlm.gc  — generic TTL-based eviction of sessions, cache, and runtime state
  CognitiveGC — semantic tombstoning of episodic traces while preserving ALCOA+
                compliance requirements (Attributable, Legible, Enduring)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.learn.memory.manager import MemoryManager

logger = logging.getLogger("monkey_brain.memory.cognitive_gc")


@dataclass
class GCResult:
    tombstoned: int = 0
    errors: list[str] = field(default_factory=list)
    swept_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tombstoned": self.tombstoned,
            "errors": self.errors,
            "swept_at": self.swept_at,
        }


class CognitiveGC:
    """Hermetic GC: evicts stale episodic context, preserves provenance tombstones.

    For each stale EpisodicTrace node it:
      1. Deletes the vector embedding (frees nearest-neighbour search space).
      2. Nulls the raw payload (frees graph DB storage).
      3. Writes a GC_TOMBSTONE status with the original auth_hash (audit trail).

    The tombstone keeps the node identity, all relationship edges, and the
    auth_hash — enough to satisfy regulatory audit requirements without keeping
    the full conversation or payload.
    """

    def __init__(self, memory_manager: MemoryManager) -> None:
        self.mgr = memory_manager

    def collect_garbage(self) -> GCResult:
        """Scan for stale episodic nodes and tombstone them."""
        now = time.time()
        stale_cutoff = now - self.mgr.staleness_threshold_seconds
        result = GCResult()

        stale_records = self.mgr.graph_db.query(
            "MATCH (e:EpisodicTrace) "
            "WHERE e.last_accessed < $cutoff "
            "RETURN e.id AS id, e.provenance_token AS provenance_token",
            parameters={"cutoff": stale_cutoff},
        )

        for record in stale_records:
            node_id = record["id"]
            provenance = record["provenance_token"]

            try:
                self._evict_vector(node_id)
                self._null_payload(node_id)
                self._write_tombstone(node_id, provenance)
                result.tombstoned += 1
                logger.debug("GC_TOMBSTONE written for node %s", node_id)
            except Exception as exc:
                msg = f"node={node_id}: {exc}"
                result.errors.append(msg)
                logger.warning("CognitiveGC error: %s", msg)

        return result

    def _evict_vector(self, node_id: str) -> None:
        """Remove the embedding from the vector index to clear search noise."""
        self.mgr.vector_db.delete(id=node_id)

    def _null_payload(self, node_id: str) -> None:
        """Erase the raw payload from the operational graph."""
        self.mgr.graph_db.execute(
            "MATCH (e:EpisodicTrace {id: $node_id}) SET e.payload = NULL",
            parameters={"node_id": node_id},
        )

    def _write_tombstone(self, node_id: str, provenance: Any) -> None:
        """Mutate the node to a minimal tombstone, retaining the audit hash."""
        auth_hash = provenance.auth_hash if hasattr(provenance, "auth_hash") else str(provenance)
        self.mgr.graph_db.execute(
            "MATCH (e:EpisodicTrace {id: $node_id}) "
            "SET e.status = 'GC_TOMBSTONE', "
            "    e.audit_trail_retained = $auth_hash",
            parameters={"node_id": node_id, "auth_hash": auth_hash},
        )
