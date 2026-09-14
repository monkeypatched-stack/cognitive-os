"""Structural memory primitives.

ProvenanceToken — immutable audit marker; never garbage collected.
MemoryNode      — typed payload container for all four memory layers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class ProvenanceToken:
    """Immutable audit trail marker.

    Survives all compaction and GC sweeps. The auth_hash ties the record to
    a specific policy evaluation, making it traceable to the governance chain
    that authorised the original memory write.
    """

    trace_id: str
    policy_path: str
    auth_hash: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class MemoryNode:
    """A typed payload node in the cognitive memory graph.

    memory_type controls which storage tier the node lives in:
      "working"    — in-process dict; evicted after the task completes
      "episodic"   — long-term trace store; compressible, provenance-immutable
      "semantic"   — distilled world-model facts; versioned, never deleted
      "procedural" — compiled routing shortcuts from BackgroundCompactor
    """

    node_id: str
    memory_type: str  # working | episodic | semantic | procedural
    payload: dict[str, Any]
    vector_embedding: Optional[List[float]] = None
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    provenance: Optional[ProvenanceToken] = None
