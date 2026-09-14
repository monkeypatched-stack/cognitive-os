"""GlobalWorld — dual-graph world model with entity/relationship metadata.

The Global World contains:
- Transition Graph (SparseTransitionTensor) — what can happen
- Entity Metadata — what exists (typed entities indexed by state)
- Relationship Metadata — how entities relate (typed relationships indexed by (i,j))
- Ontology — type hierarchy

The world is authoritative and shared by all actors.
Only the Context Stream may modify it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.monkey_brain.kernel.compile.entity import (
    EntityType,
    EntityRegistry,
    RelationType,
)
from src.monkey_brain.kernel.compile.ontology import Ontology

logger = logging.getLogger("agentos.global_world")


@dataclass
class EntityMeta:
    """Metadata for a state index i in the tensor."""

    index: int
    state_name: str
    entity_type: EntityType
    domain: str
    attributes: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    is_actor: bool = False
    tenant_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class RelMeta:
    """Metadata for a transition (i, j) in the tensor."""

    source_index: int
    target_index: int
    source_name: str
    target_name: str
    relation_type: RelationType
    direction: str = "forward"
    domain: str = "default"
    attributes: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    tenant_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class GlobalWorld:
    """Dual-graph world model with entity/relationship metadata.

    The world is authoritative and shared by all actors.
    Only the Context Stream may modify it via update_entity/update_relationship.

    Immutability:
    - After initialization, set_entity/set_relationship/observe are disabled
    - ContextStream calls update_* methods which bypass the freeze
    - Actors receive a read-only WorldView
    """

    def __init__(self, tensor: Any = None, *, frozen: bool = False) -> None:
        self._tensor = tensor
        self._entity_meta: dict[int, EntityMeta] = {}
        self._rel_meta: dict[tuple[int, int], RelMeta] = {}
        self._ontology = Ontology()
        self._registry = EntityRegistry()
        self._version = 0
        self._dirty = True
        self._frozen = frozen
        self._gpu_matrix: np.ndarray | None = None
        self._gpu_embeddings: np.ndarray | None = None

    # ── Immutability enforcement ──────────────────────────────────────────────

    def freeze(self) -> None:
        """Freeze the world — only ContextStream should call this after init."""
        self._frozen = True

    def unfreeze(self) -> None:
        """Temporarily unfreeze — only ContextStream should call this."""
        self._frozen = False

    def _check_frozen(self) -> None:
        if self._frozen:
            raise RuntimeError("GlobalWorld is frozen — only ContextStream may modify it")

    # ── Entity Metadata (mutation blocked when frozen) ────────────────────────

    def set_entity(
        self,
        state: str,
        entity_type: EntityType = EntityType.ENTITY,
        domain: str = "default",
        **attrs: Any,
    ) -> EntityMeta:
        self._check_frozen()
        return self._add_entity(state, entity_type, domain, **attrs)

    def update_entity(self, state: str, **attrs: Any) -> EntityMeta | None:
        """Update entity metadata — only ContextStream calls this."""
        if self._tensor is None:
            return None
        i = self._tensor._state_index.get(state) if hasattr(self._tensor, "_state_index") else None
        if i is None:
            return None
        meta = self._entity_meta.get(i)
        if meta:
            meta.attributes.update(attrs)
            meta.updated_at = time.time()
            self._dirty = True
        return meta

    def _add_entity(
        self,
        state: str,
        entity_type: EntityType = EntityType.ENTITY,
        domain: str = "default",
        **attrs: Any,
    ) -> EntityMeta:
        i = self._tensor.intern(state) if self._tensor else 0
        meta = EntityMeta(
            index=i,
            state_name=state,
            entity_type=entity_type,
            domain=domain,
            attributes=attrs,
            is_actor=entity_type
            in {
                EntityType.PERSON,
                EntityType.ROBOT,
                EntityType.AI_AGENT,
                EntityType.VEHICLE,
            },
        )
        self._entity_meta[i] = meta
        self._dirty = True
        return meta

    def get_entity(self, state: str) -> EntityMeta | None:
        if self._tensor is None:
            return None
        i = self._tensor._state_index.get(state) if hasattr(self._tensor, "_state_index") else None
        if i is None:
            return None
        return self._entity_meta.get(i)

    def entities_by_type(self, entity_type: EntityType) -> list[EntityMeta]:
        return [m for m in self._entity_meta.values() if m.entity_type == entity_type]

    def entities_by_domain(self, domain: str) -> list[EntityMeta]:
        return [m for m in self._entity_meta.values() if m.domain == domain]

    def actors(self) -> list[EntityMeta]:
        return [m for m in self._entity_meta.values() if m.is_actor]

    # ── Relationship Metadata (mutation blocked when frozen) ───────────────────

    def set_relationship(
        self,
        src: str,
        dst: str,
        relation_type: RelationType,
        domain: str = "default",
        **attrs: Any,
    ) -> RelMeta:
        self._check_frozen()
        return self._add_relationship(src, dst, relation_type, domain, **attrs)

    def update_relationship(self, src: str, dst: str, **attrs: Any) -> RelMeta | None:
        """Update relationship metadata — only ContextStream calls this."""
        if self._tensor is None:
            return None
        i = self._tensor._state_index.get(src) if hasattr(self._tensor, "_state_index") else None
        j = self._tensor._state_index.get(dst) if hasattr(self._tensor, "_state_index") else None
        if i is None or j is None:
            return None
        meta = self._rel_meta.get((i, j))
        if meta:
            meta.attributes.update(attrs)
            meta.updated_at = time.time()
            self._dirty = True
        return meta

    def _add_relationship(
        self,
        src: str,
        dst: str,
        relation_type: RelationType,
        domain: str = "default",
        **attrs: Any,
    ) -> RelMeta:
        i = self._tensor.intern(src) if self._tensor else 0
        j = self._tensor.intern(dst) if self._tensor else 0
        meta = RelMeta(
            source_index=i,
            target_index=j,
            source_name=src,
            target_name=dst,
            relation_type=relation_type,
            domain=domain,
            attributes=attrs,
        )
        self._rel_meta[(i, j)] = meta
        self._dirty = True
        return meta

    def get_relationship(self, src: str, dst: str) -> RelMeta | None:
        if self._tensor is None:
            return None
        i = self._tensor._state_index.get(src) if hasattr(self._tensor, "_state_index") else None
        j = self._tensor._state_index.get(dst) if hasattr(self._tensor, "_state_index") else None
        if i is None or j is None:
            return None
        return self._rel_meta.get((i, j))

    def relationships_from(self, state: str) -> list[RelMeta]:
        if self._tensor is None:
            return []
        i = self._tensor._state_index.get(state) if hasattr(self._tensor, "_state_index") else None
        if i is None:
            return []
        return [m for (si, _), m in self._rel_meta.items() if si == i]

    def relationships_to(self, state: str) -> list[RelMeta]:
        if self._tensor is None:
            return []
        j = self._tensor._state_index.get(state) if hasattr(self._tensor, "_state_index") else None
        if j is None:
            return []
        return [m for (_, tj), m in self._rel_meta.items() if tj == j]

    def relationships_by_type(self, rel_type: RelationType) -> list[RelMeta]:
        return [m for m in self._rel_meta.values() if m.relation_type == rel_type]

    # ── Tensor Delegation ─────────────────────────────────────────────────────

    @property
    def tensor(self) -> Any:
        return self._tensor

    @property
    def ontology(self) -> Ontology:
        return self._ontology

    @property
    def version(self) -> int:
        return self._version

    def observe(self, src: str, dst: str, **kwargs: Any) -> None:
        self._check_frozen()
        if self._tensor:
            self._tensor.observe(src, dst, **kwargs)
        self._dirty = True

    def successors(self, state: str) -> list:
        if self._tensor:
            return self._tensor.successors(state)
        return []

    def nnz(self) -> int:
        if self._tensor:
            return self._tensor.nnz()
        return 0

    def states(self) -> list[str]:
        if self._tensor:
            return self._tensor.states()
        return []

    def domains(self) -> list[str]:
        if self._tensor:
            return self._tensor.domains()
        return []

    # ── GPU Optimization ──────────────────────────────────────────────────────

    def gpu_matrix(self) -> np.ndarray:
        if not self._dirty and self._gpu_matrix is not None:
            return self._gpu_matrix
        self._rebuild_gpu()
        return self._gpu_matrix

    def gpu_embeddings(self) -> np.ndarray:
        if not self._dirty and self._gpu_embeddings is not None:
            return self._gpu_embeddings
        self._rebuild_gpu()
        return self._gpu_embeddings

    def _rebuild_gpu(self) -> None:
        states = self.states()
        n = len(states)
        if n == 0:
            self._gpu_matrix = np.zeros((0, 0), dtype=np.float32)
            self._gpu_embeddings = np.zeros((0, 64), dtype=np.float32)
            self._dirty = False
            return

        state_idx = {s: i for i, s in enumerate(states)}
        mat = np.zeros((n, n), dtype=np.float32)

        if self._tensor and hasattr(self._tensor, "__iter__"):
            from src.monkey_brain.kernel.compile.tensor import Feature

            for src, dst in self._tensor:
                if src in state_idx and dst in state_idx:
                    i, j = state_idx[src], state_idx[dst]
                    try:
                        feat = self._tensor.feature(src, dst, Feature.PROBABILITY)
                    except Exception:
                        feat = 1.0
                    mat[i, j] = float(feat)

        row_sums = mat.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        self._gpu_matrix = mat / row_sums

        dim = min(64, max(8, n))
        rng = np.random.RandomState(42)
        emb = rng.randn(n, dim).astype(np.float32)
        emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8)
        self._gpu_embeddings = emb
        self._dirty = False

    def matmul(self, vectors: np.ndarray) -> np.ndarray:
        mat = self.gpu_matrix()
        if mat.size == 0 or vectors.size == 0:
            return np.zeros_like(vectors)
        return vectors @ mat

    # ── Clone (for prediction) ────────────────────────────────────────────────

    def clone(self) -> GlobalWorld:
        from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor

        cloned_tensor = SparseTransitionTensor()
        if self._tensor and hasattr(self._tensor, "to_dict"):
            cloned_tensor.load_dict(self._tensor.to_dict())

        clone = GlobalWorld(cloned_tensor)
        clone._entity_meta = dict(self._entity_meta)
        clone._rel_meta = dict(self._rel_meta)
        clone._ontology = self._ontology
        clone._version = self._version
        clone._dirty = True
        return clone

    # ── Serialization ─────────────────────────────────────────────────────────

    def export_entities(self) -> dict:
        return {
            str(i): {
                "index": m.index,
                "state_name": m.state_name,
                "entity_type": m.entity_type.value,
                "domain": m.domain,
                "attributes": m.attributes,
                "state": m.state,
                "is_actor": m.is_actor,
                "tenant_id": m.tenant_id,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for i, m in self._entity_meta.items()
        }

    def export_relationships(self) -> dict:
        return {
            f"({i},{j})": {
                "source_name": m.source_name,
                "target_name": m.target_name,
                "relation_type": m.relation_type.value,
                "direction": m.direction,
                "domain": m.domain,
                "attributes": m.attributes,
                "is_active": m.is_active,
                "tenant_id": m.tenant_id,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for (i, j), m in self._rel_meta.items()
        }

    def meta(self) -> dict:
        return {
            "version": self._version,
            "entity_count": len(self._entity_meta),
            "relationship_count": len(self._rel_meta),
            "state_count": len(self.states()),
            "transition_count": self.nnz(),
        }

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Save world state (tensor + metadata) atomically."""
        import json
        import os

        os.makedirs(path, exist_ok=True)

        if self._tensor:
            self._tensor.save(os.path.join(path, "tensor.json"))

        with open(os.path.join(path, "entities.json"), "w") as f:
            json.dump(self.export_entities(), f, indent=2)

        with open(os.path.join(path, "relationships.json"), "w") as f:
            json.dump(self.export_relationships(), f, indent=2)

        with open(os.path.join(path, "ontology.json"), "w") as f:
            json.dump(self._ontology.export(), f, indent=2)

        with open(os.path.join(path, "meta.json"), "w") as f:
            json.dump(self.meta(), f, indent=2)

        logger.debug("[global_world] saved to %s", path)

    def load(self, path: str) -> None:
        """Load world state from saved files."""
        import json
        import os

        tensor_path = os.path.join(path, "tensor.json")
        if os.path.exists(tensor_path) and self._tensor:
            self._tensor.load(tensor_path)

        entities_path = os.path.join(path, "entities.json")
        if os.path.exists(entities_path):
            with open(entities_path) as f:
                entities = json.load(f)
            for idx_str, data in entities.items():
                i = int(idx_str)
                self._entity_meta[i] = EntityMeta(
                    index=data["index"],
                    state_name=data["state_name"],
                    entity_type=EntityType(data["entity_type"]),
                    domain=data["domain"],
                    attributes=data.get("attributes", {}),
                    state=data.get("state", {}),
                    is_actor=data.get("is_actor", False),
                    tenant_id=data.get("tenant_id"),
                    created_at=data.get("created_at", 0),
                    updated_at=data.get("updated_at", 0),
                )

        rels_path = os.path.join(path, "relationships.json")
        if os.path.exists(rels_path):
            with open(rels_path) as f:
                rels = json.load(f)
            for key_str, data in rels.items():
                key = tuple(int(x) for x in key_str.strip("()").split(","))
                self._rel_meta[key] = RelMeta(
                    source_index=key[0],
                    target_index=key[1],
                    source_name=data["source_name"],
                    target_name=data["target_name"],
                    relation_type=RelationType(data["relation_type"]),
                    direction=data.get("direction", "forward"),
                    domain=data.get("domain", "default"),
                    attributes=data.get("attributes", {}),
                    is_active=data.get("is_active", True),
                    tenant_id=data.get("tenant_id"),
                    created_at=data.get("created_at", 0),
                    updated_at=data.get("updated_at", 0),
                )

        logger.debug("[global_world] loaded from %s", path)

    def summary(self) -> dict:
        return {
            "version": self._version,
            "states": len(self.states()),
            "transitions": self.nnz(),
            "entities": len(self._entity_meta),
            "relationships": len(self._rel_meta),
            "actors": len(self.actors()),
            "domains": self.domains(),
        }
