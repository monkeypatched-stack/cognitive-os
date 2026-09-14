"""StateNode — immutable versioned state snapshot for the reducer workload.

Written once; never altered.  Moving between tiers (active → backup) does not
mutate the record — only its location in the workload changes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class StateNode:
    """One immutable record in the reducer log.

    parent_id links this node to the state it was derived from, forming a
    traversable provenance chain even after eviction from the active tier.
    """

    node_id: str = field(default_factory=lambda: uuid4().hex)
    parent_id: str | None = None
    action_type: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    ttl: float = 300.0  # seconds before eviction from active context
    checksum: str = ""

    def __post_init__(self) -> None:
        if not self.checksum:
            raw = json.dumps(self.state, sort_keys=True, default=str).encode()
            object.__setattr__(self, "checksum", hashlib.sha256(raw).hexdigest()[:16])

    @property
    def is_expired(self) -> bool:
        return time.time() > self.created_at + self.ttl

    @property
    def age(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "action_type": self.action_type,
            "state": self.state,
            "created_at": self.created_at,
            "ttl": self.ttl,
            "checksum": self.checksum,
            "age": round(self.age, 3),
            "is_expired": self.is_expired,
        }
